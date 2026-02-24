"""
Knowledge API — 닥터브릉이의 지식 조회 및 관리
트레이너가 지식을 가르치고, 클라이언트가 지식을 조회
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
import json

from app.db.database import get_db, SoundKnowledge
from app.services.knowledge_service import KnowledgeService
from app.services.brain import DrVroomBrain
from app.services.ws_manager import ws_manager

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class TeachRequest(BaseModel):
    """트레이너/전문가가 지식을 가르치는 요청"""
    trainer_id: str
    session_id: Optional[str] = None
    component: str
    correct_status: str                  # normal/warning/critical
    correct_fault_code: str
    notes: str
    vehicle_type: str = "unknown"
    # 오디오 특징 (직접 입력 또는 세션에서 추출)
    samples: Optional[List[float]] = None
    dominant_freq: Optional[float] = None
    rms: Optional[float] = None


class KnowledgeItem(BaseModel):
    id: str
    component: str
    status: str
    fault_code: str
    description: str
    confidence: float
    dominant_freq: float
    sample_count: int
    confirmed_by_expert: bool
    source: str


@router.post("/teach")
async def teach_knowledge(
    req: TeachRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    전문가/트레이너가 닥터브릉이에게 지식을 가르침
    "이 소리는 이거야!" — 확실한 정답 제공
    """
    # 오디오 특징 추출
    if req.samples:
        features = DrVroomBrain.extract_features(req.samples)
    else:
        # 수동으로 특징값 입력
        features = {
            "dominant_freq": req.dominant_freq or 0,
            "rms": req.rms or 0,
            "peak": 0,
            "band_energy": {},
            "freq_peaks": [],
        }

    result = await KnowledgeService.apply_expert_label(
        db=db,
        session_id=req.session_id or "manual",
        component=req.component,
        correct_status=req.correct_status,
        correct_fault_code=req.correct_fault_code,
        notes=req.notes,
        trainer_id=req.trainer_id,
        features=features,
        vehicle_type=req.vehicle_type,
    )

    # 모든 클라이언트에게 새 지식 알림
    await ws_manager.broadcast_knowledge_update({
        "component": req.component,
        "status": req.correct_status,
        "knowledge_id": result["knowledge_id"],
        "source": "trainer",
    })

    return {
        "success": True,
        "message": f"닥터브릉이가 배웠어요! '{req.component}' 부품의 '{req.correct_status}' 패턴을 기억했습니다.",
        **result,
    }


@router.get("/stats")
async def get_knowledge_stats(db: AsyncSession = Depends(get_db)):
    """닥터브릉이의 성장 현황"""
    stats = await KnowledgeService.get_knowledge_stats(db)
    return {
        "message": "닥터브릉이 성장 현황",
        "stats": stats,
    }


@router.get("/list")
async def list_knowledge(
    component: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    expert_only: bool = Query(False),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """지식 목록 조회"""
    query = select(SoundKnowledge)
    if component:
        query = query.where(SoundKnowledge.component == component)
    if status:
        query = query.where(SoundKnowledge.status == status)
    if expert_only:
        query = query.where(SoundKnowledge.confirmed_by_expert == True)
    query = query.order_by(SoundKnowledge.confidence.desc()).limit(limit)

    result = await db.execute(query)
    items = result.scalars().all()

    return [
        KnowledgeItem(
            id=item.id,
            component=item.component,
            status=item.status,
            fault_code=item.fault_code or "",
            description=item.description or "",
            confidence=item.confidence,
            dominant_freq=item.dominant_freq or 0,
            sample_count=item.sample_count,
            confirmed_by_expert=item.confirmed_by_expert,
            source=item.source or "client",
        )
        for item in items
    ]


@router.delete("/{knowledge_id}")
async def delete_knowledge(
    knowledge_id: str,
    trainer_id: str,
    db: AsyncSession = Depends(get_db),
):
    """잘못된 지식 삭제 (트레이너만 가능)"""
    result = await db.execute(
        select(SoundKnowledge).where(SoundKnowledge.id == knowledge_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Knowledge item not found")

    await db.delete(item)
    return {"success": True, "message": f"Knowledge {knowledge_id} deleted"}
