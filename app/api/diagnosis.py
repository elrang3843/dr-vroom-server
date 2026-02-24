"""
Diagnosis API — 진단 요청 처리
클라이언트가 보낸 소리 데이터를 분석하고 결과를 반환
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
import uuid
import json
from datetime import datetime

from app.db.database import get_db, DiagnosisSession
from app.services.brain import DrVroomBrain
from app.services.knowledge_service import KnowledgeService
from app.services.ws_manager import ws_manager
from app.core.config import settings

router = APIRouter(prefix="/api/v1/diagnosis", tags=["diagnosis"])


# ─── Request/Response Models ──────────────────────────────────────────────────

class DiagnosisRequest(BaseModel):
    client_id: str = Field(..., description="클라이언트 기기 ID")
    session_type: str = Field("client", description="client/trainer/expert")
    vehicle_info: str = Field("Unknown Vehicle", description="차량 정보")
    vehicle_type: str = Field("unknown", description="sedan/suv/truck/etc")
    vehicle_brand: str = Field("unknown", description="toyota/hyundai/etc")
    samples: List[float] = Field(..., description="오디오 샘플 데이터")
    duration_seconds: int = Field(5, description="녹음 시간(초)")
    use_demo: bool = Field(False, description="데모 모드")
    demo_fault: Optional[str] = Field(None, description="데모 고장 유형")


class ComponentResult(BaseModel):
    component: str
    status: str
    fault_code: str
    description: str
    confidence: float
    dominant_freq: float
    knowledge_based: bool


class DiagnosisResponse(BaseModel):
    session_id: str
    overall_status: str
    health_score: float
    component_results: List[ComponentResult]
    features_summary: Dict
    knowledge_updated: bool
    message: str
    timestamp: str


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/analyze", response_model=DiagnosisResponse)
async def analyze_vibration(
    req: DiagnosisRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    메인 진단 엔드포인트
    1. 오디오 특징 추출
    2. 지식 DB 조회
    3. 각 부품 진단
    4. 결과를 지식 DB에 저장 (학습)
    """
    # 데모 모드: 신호 생성
    if req.use_demo or not req.samples:
        samples = DrVroomBrain.generate_demo_signal(
            inject_fault=req.demo_fault is not None,
            fault_type=req.demo_fault or "bearing",
            duration_ms=req.duration_seconds * 1000,
        )
    else:
        samples = req.samples

    # 1. 오디오 특징 추출
    features = DrVroomBrain.extract_features(samples)
    if not features:
        raise HTTPException(status_code=422, detail="Failed to extract audio features")

    # 2. 각 부품별 진단
    component_results = []
    knowledge_ids = []
    knowledge_updated = False

    for component in DrVroomBrain.FREQ_BANDS:
        # 관련 지식 검색
        similar = await KnowledgeService.find_similar_knowledge(
            db=db,
            component=component,
            features=features,
            vehicle_type=req.vehicle_type,
        )

        # 진단 실행
        result = DrVroomBrain.diagnose_component(
            component=component,
            features=features,
            knowledge_items=similar if similar else None,
        )

        component_results.append({
            "component": component,
            **result,
        })

        # 3. 결과를 지식 DB에 저장 (자동 학습)
        if result.get("confidence", 0) >= settings.MIN_CONFIDENCE:
            kid = await KnowledgeService.save_or_update_knowledge(
                db=db,
                component=component,
                features=features,
                status=result["status"],
                fault_code=result.get("fault_code", ""),
                description=result.get("description", ""),
                confidence=result["confidence"],
                vehicle_type=req.vehicle_type,
                vehicle_brand=req.vehicle_brand,
                source=req.session_type,
            )
            knowledge_ids.append(kid)
            knowledge_updated = True

    # 4. 전체 건강 점수
    health_score = DrVroomBrain.calculate_health_score(component_results)
    overall_status = (
        "critical" if any(r["status"] == "critical" for r in component_results)
        else "warning" if any(r["status"] == "warning" for r in component_results)
        else "normal"
    )

    # 5. 세션 저장
    session_id = str(uuid.uuid4())
    session = DiagnosisSession(
        id=session_id,
        client_id=req.client_id,
        session_type=req.session_type,
        vehicle_info=req.vehicle_info,
        vehicle_type=req.vehicle_type,
        waveform_summary=json.dumps({
            "rms": features.get("rms", 0),
            "peak": features.get("peak", 0),
            "dominant_freq": features.get("dominant_freq", 0),
        }),
        frequency_peaks=json.dumps(features.get("freq_peaks", [])),
        duration_seconds=req.duration_seconds,
        component_results=json.dumps(component_results),
        overall_status=overall_status,
        health_score=health_score,
        contributed_to_knowledge=knowledge_updated,
        knowledge_ids=json.dumps(knowledge_ids),
    )
    db.add(session)

    # 6. WebSocket으로 결과 알림
    if knowledge_updated:
        await ws_manager.broadcast_knowledge_update({
            "component_count": len(knowledge_ids),
            "overall_status": overall_status,
        })

    faults = [r for r in component_results if r["status"] != "normal"]
    msg = (
        f"닥터브릉이가 {len(faults)}개의 이상을 발견했어요!" if faults
        else "모든 부품이 정상이에요! 건강한 차량입니다."
    )

    return DiagnosisResponse(
        session_id=session_id,
        overall_status=overall_status,
        health_score=health_score,
        component_results=[ComponentResult(**r) for r in component_results],
        features_summary={
            "rms": round(features.get("rms", 0), 4),
            "peak": round(features.get("peak", 0), 4),
            "dominant_freq": round(features.get("dominant_freq", 0), 1),
            "spectral_centroid": round(features.get("spectral_centroid", 0), 1),
        },
        knowledge_updated=knowledge_updated,
        message=msg,
        timestamp=datetime.utcnow().isoformat(),
    )


@router.get("/demo/{fault_type}")
async def get_demo_diagnosis(
    fault_type: str,
    db: AsyncSession = Depends(get_db),
):
    """데모 진단 (교육/테스트용)"""
    req = DiagnosisRequest(
        client_id="demo",
        session_type="client",
        vehicle_info="Demo Vehicle",
        vehicle_type="sedan",
        vehicle_brand="demo",
        samples=[],
        use_demo=True,
        demo_fault=fault_type if fault_type != "normal" else None,
    )
    return await analyze_vibration(req, db)


@router.get("/history/{client_id}")
async def get_client_history(
    client_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """클라이언트 진단 이력 조회"""
    from sqlalchemy import select
    query = (
        select(DiagnosisSession)
        .where(DiagnosisSession.client_id == client_id)
        .order_by(DiagnosisSession.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    sessions = result.scalars().all()

    return [
        {
            "session_id": s.id,
            "vehicle_info": s.vehicle_info,
            "overall_status": s.overall_status,
            "health_score": s.health_score,
            "duration_seconds": s.duration_seconds,
            "contributed_to_knowledge": s.contributed_to_knowledge,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in sessions
    ]
