"""
Knowledge Service — 닥터브릉이의 기억 저장소
"배운 것을 저장하고, 다음에 같은 소리를 들으면 기억을 떠올린다"
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from typing import List, Optional, Dict
import uuid
import json
from datetime import datetime

from app.db.database import SoundKnowledge, DiagnosisSession, TrainingLabel
from app.services.brain import DrVroomBrain
from app.core.config import settings


class KnowledgeService:

    @staticmethod
    async def find_similar_knowledge(
        db: AsyncSession,
        component: str,
        features: Dict,
        vehicle_type: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """
        유사한 지식 항목 검색
        "이 소리, 전에 들어본 적 있어?"
        """
        dominant_freq = features.get("dominant_freq", 0)
        rms = features.get("rms", 0)

        # 주파수 대역 ±30% 범위에서 검색
        freq_min = dominant_freq * 0.7
        freq_max = dominant_freq * 1.3

        query = select(SoundKnowledge).where(
            SoundKnowledge.component == component,
            SoundKnowledge.dominant_freq >= freq_min,
            SoundKnowledge.dominant_freq <= freq_max,
            SoundKnowledge.confidence >= settings.MIN_CONFIDENCE,
        )

        if vehicle_type:
            query = query.where(SoundKnowledge.vehicle_type == vehicle_type)

        query = query.order_by(SoundKnowledge.confidence.desc()).limit(limit * 2)
        result = await db.execute(query)
        candidates = result.scalars().all()

        # 유사도 계산 후 정렬
        scored = []
        for item in candidates:
            item_features = {
                "dominant_freq": item.dominant_freq,
                "rms": item.rms_amplitude,
                "band_energy": json.loads(item.freq_band_energy or "{}"),
            }
            sim = DrVroomBrain.compute_feature_similarity(features, item_features)
            scored.append((sim, item))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "id": item.id,
                "component": item.component,
                "status": item.status,
                "fault_code": item.fault_code,
                "description": item.description,
                "confidence": item.confidence,
                "dominant_freq": item.dominant_freq,
                "rms_amplitude": item.rms_amplitude,
                "sample_count": item.sample_count,
                "similarity": sim,
                "confirmed_by_expert": item.confirmed_by_expert,
            }
            for sim, item in scored[:limit]
        ]

    @staticmethod
    async def save_or_update_knowledge(
        db: AsyncSession,
        component: str,
        features: Dict,
        status: str,
        fault_code: str,
        description: str,
        confidence: float,
        vehicle_type: str = "unknown",
        vehicle_brand: str = "unknown",
        source: str = "client",
        confirmed_by_expert: bool = False,
    ) -> str:
        """
        지식 저장 또는 업데이트
        "같은 경험이 쌓이면 확신이 높아진다"
        """
        dominant_freq = features.get("dominant_freq", 0)
        rms = features.get("rms", 0)
        band_energy = features.get("band_energy", {})

        # 기존 유사 지식 탐색
        freq_min = dominant_freq * 0.85
        freq_max = dominant_freq * 1.15
        rms_min = rms * 0.7
        rms_max = rms * 1.3

        existing_query = select(SoundKnowledge).where(
            SoundKnowledge.component == component,
            SoundKnowledge.status == status,
            SoundKnowledge.dominant_freq >= freq_min,
            SoundKnowledge.dominant_freq <= freq_max,
            SoundKnowledge.rms_amplitude >= rms_min,
            SoundKnowledge.rms_amplitude <= rms_max,
        ).limit(1)

        result = await db.execute(existing_query)
        existing = result.scalar_one_or_none()

        if existing:
            # 기존 지식 강화 (경험이 쌓일수록 확신↑)
            new_count = existing.sample_count + 1
            # 지수 이동 평균으로 업데이트
            alpha = 0.3
            new_conf = min(0.97, existing.confidence * (1 - alpha) + confidence * alpha)
            new_dom_freq = existing.dominant_freq * (1 - alpha) + dominant_freq * alpha
            new_rms = existing.rms_amplitude * (1 - alpha) + rms * alpha

            await db.execute(
                update(SoundKnowledge)
                .where(SoundKnowledge.id == existing.id)
                .values(
                    sample_count=new_count,
                    confidence=new_conf,
                    dominant_freq=new_dom_freq,
                    rms_amplitude=new_rms,
                    freq_band_energy=json.dumps(band_energy),
                    last_updated=datetime.utcnow(),
                    confirmed_by_expert=confirmed_by_expert or existing.confirmed_by_expert,
                )
            )
            return existing.id
        else:
            # 새로운 지식 저장
            knowledge_id = str(uuid.uuid4())
            new_knowledge = SoundKnowledge(
                id=knowledge_id,
                vehicle_type=vehicle_type,
                vehicle_brand=vehicle_brand,
                component=component,
                dominant_freq=dominant_freq,
                rms_amplitude=rms,
                peak_amplitude=features.get("peak", 0),
                spectral_centroid=features.get("spectral_centroid", 0),
                zero_crossing_rate=features.get("zero_crossing_rate", 0),
                freq_band_energy=json.dumps(band_energy),
                status=status,
                fault_code=fault_code,
                description=description,
                confidence=confidence,
                sample_count=1,
                confirmed_by_expert=confirmed_by_expert,
                source=source,
            )
            db.add(new_knowledge)
            return knowledge_id

    @staticmethod
    async def apply_expert_label(
        db: AsyncSession,
        session_id: str,
        component: str,
        correct_status: str,
        correct_fault_code: str,
        notes: str,
        trainer_id: str,
        features: Dict,
        vehicle_type: str = "unknown",
    ) -> Dict:
        """
        전문가 레이블 적용 — 선생님의 가르침
        "이 소리는 이거야!" — 확실한 답을 알려줌
        """
        label_id = str(uuid.uuid4())
        label = TrainingLabel(
            id=label_id,
            session_id=session_id,
            trainer_id=trainer_id,
            component=component,
            correct_status=correct_status,
            correct_fault_code=correct_fault_code,
            notes=notes,
            freq_signature=json.dumps(features.get("freq_peaks", [])),
            verified=True,
        )
        db.add(label)

        # 전문가 레이블은 높은 신뢰도로 즉시 저장
        knowledge_id = await KnowledgeService.save_or_update_knowledge(
            db=db,
            component=component,
            features=features,
            status=correct_status,
            fault_code=correct_fault_code,
            description=notes,
            confidence=0.95,
            vehicle_type=vehicle_type,
            source="trainer",
            confirmed_by_expert=True,
        )

        return {"label_id": label_id, "knowledge_id": knowledge_id}

    @staticmethod
    async def get_knowledge_stats(db: AsyncSession) -> Dict:
        """닥터브릉이의 성장 현황"""
        total = await db.execute(select(func.count(SoundKnowledge.id)))
        expert_confirmed = await db.execute(
            select(func.count(SoundKnowledge.id)).where(
                SoundKnowledge.confirmed_by_expert == True
            )
        )
        avg_conf = await db.execute(select(func.avg(SoundKnowledge.confidence)))
        total_sessions = await db.execute(select(func.count(DiagnosisSession.id)))

        components = await db.execute(
            select(SoundKnowledge.component, func.count(SoundKnowledge.id))
            .group_by(SoundKnowledge.component)
        )

        return {
            "total_knowledge": total.scalar() or 0,
            "expert_confirmed": expert_confirmed.scalar() or 0,
            "avg_confidence": round(avg_conf.scalar() or 0, 3),
            "total_sessions": total_sessions.scalar() or 0,
            "by_component": {row[0]: row[1] for row in components.fetchall()},
        }
