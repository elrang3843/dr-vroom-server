"""
Dr. Vroom — 포인트 기반 보험 API (Points-Based Insurance)
══════════════════════════════════════════════════════════

법적 지위:
  이 보험 제도는 앱 내 가상재화(포인트) 기반의 '상호부조 보상 프로그램'입니다.
  보험업법상 보험 상품이 아니며, 금융감독원 감독 대상이 아닙니다.
  전 세계 어느 국가에서도 보험업 허가 없이 운영 가능한 구조입니다.

글로벌 설계:
  - 보험료 = 포인트 (현금/통화 아님)
  - 보상금 = 포인트 (현금/통화 아님)
  - 환율 없음, 국가별 법규 적용 없음
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import uuid

from app.db.database import get_db, User
from app.db.points_schema import (
    InsurancePolicyV2, InsuranceClaimV2, InsurancePool,
    InsuranceTier, PointTxType
)
from app.services.points_service import PointsService, INSURANCE_PLANS
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/v1/insurance", tags=["insurance"])


# ── Pydantic ─────────────────────────────────────────────────────

class PlanOut(BaseModel):
    tier: str
    label: str
    label_ko: str
    emoji: str
    monthly_pts: int
    coverage_pts: int
    annual_pts: int
    description: str
    legal_note: str


class PolicyOut(BaseModel):
    id: str
    tier: str
    monthly_premium_pts: int
    coverage_per_case_pts: int
    remaining_annual_pts: int
    is_active: bool
    start_date: datetime
    end_date: Optional[datetime]
    total_claims: int
    total_paid_pts: int


class EnrollReq(BaseModel):
    tier: str
    auto_renew: bool = True


class ClaimReq(BaseModel):
    policy_id: str
    bounty_id: Optional[str] = None
    answer_id: Optional[str] = None
    claim_type: str
    description: str
    claimed_points: int
    evidence_urls: List[str] = []


# ── Plan definitions ──────────────────────────────────────────────

PLAN_DETAILS = {
    "none":     {"label": "None",     "label_ko": "미가입",      "emoji": "❌",
                 "description": "보험 없음",
                 "legal_note": ""},
    "basic":    {"label": "Basic",    "label_ko": "기본",        "emoji": "🛡️",
                 "description": "Free 등급 자동 포함. 가입 시 500P 보상 한도.",
                 "legal_note": "무료 상호부조 보상 프로그램"},
    "standard": {"label": "Standard", "label_ko": "표준",        "emoji": "🛡️🛡️",
                 "description": "월 50P. AI 오진단 시 최대 2,000P 보상.",
                 "legal_note": "포인트 기반 상호부조 보상 프로그램 — 금융 상품 아님"},
    "premium":  {"label": "Premium",  "label_ko": "프리미엄",    "emoji": "💎🛡️",
                 "description": "월 100P. 오진단/전문가 오답변 최대 5,000P 보상.",
                 "legal_note": "포인트 기반 상호부조 보상 프로그램 — 금융 상품 아님"},
}


# ── Endpoints ────────────────────────────────────────────────────

@router.get("/plans", summary="보험 상품 목록")
async def list_plans():
    """포인트 기반 보험 플랜 전체 목록"""
    plans = []
    for key, plan in INSURANCE_PLANS.items():
        detail = PLAN_DETAILS.get(key, {})
        plans.append({
            "tier": key,
            "label": detail.get("label", key),
            "label_ko": detail.get("label_ko", key),
            "emoji": detail.get("emoji", ""),
            "monthly_pts": plan["monthly_pts"],
            "coverage_pts": plan["coverage_pts"],
            "annual_pts": plan["annual_pts"],
            "description": detail.get("description", ""),
            "legal_note": detail.get("legal_note", ""),
        })

    return {
        "currency": "points",
        "plans": plans,
        "global_notice": (
            "모든 보험료 및 보상금은 앱 내 가상 포인트로 처리됩니다. "
            "현금 입출금이 없으며, 전 세계 동일하게 적용됩니다. "
            "This program uses virtual points only — no cash, no currency exchange."
        ),
        "legal_disclaimer": (
            "닥터브릉이 보상 프로그램은 가상재화(포인트) 기반 상호부조 제도로, "
            "보험업법 적용 대상이 아닙니다 (대한민국 기준). "
            "This is a mutual aid program using virtual goods (points), "
            "not a regulated insurance product in any jurisdiction."
        ),
    }


@router.post("/enroll", summary="보험 가입 (포인트)")
async def enroll(
    req: EnrollReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """포인트로 보험 가입 — 현금 없음, 글로벌 동일"""
    tier_key = req.tier.lower()
    if tier_key not in INSURANCE_PLANS:
        raise HTTPException(400, f"유효하지 않은 보험 등급: {req.tier}")

    plan = INSURANCE_PLANS[tier_key]
    detail = PLAN_DETAILS[tier_key]

    # 중복 가입 체크
    existing = await db.execute(
        select(InsurancePolicyV2).where(
            InsurancePolicyV2.user_id == current_user.id,
            InsurancePolicyV2.tier == InsuranceTier(tier_key),
            InsurancePolicyV2.is_active == True,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "이미 동일 등급 보험에 가입되어 있습니다.")

    # 첫 달 포인트 차감 (basic은 무료)
    if plan["monthly_pts"] > 0:
        try:
            await PointsService.spend(
                db=db,
                user_id=current_user.id,
                amount=plan["monthly_pts"],
                tx_type=PointTxType.INSURANCE_PREMIUM,
                ref_type="insurance_enroll",
                description=f"{detail['emoji']} {detail['label_ko']} 보험 가입 {plan['monthly_pts']}P",
            )
        except ValueError as e:
            raise HTTPException(402, str(e))

    policy = InsurancePolicyV2(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        tier=InsuranceTier(tier_key),
        monthly_premium_pts=plan["monthly_pts"],
        coverage_per_case_pts=plan["coverage_pts"],
        annual_limit_pts=plan["annual_pts"],
        is_active=True,
        start_date=datetime.utcnow(),
        end_date=datetime.utcnow() + timedelta(days=365),
        auto_renew=req.auto_renew,
        remaining_annual_pts=plan["annual_pts"],
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)

    return {
        "success": True,
        "policy_id": policy.id,
        "tier": tier_key,
        "label": detail["label"],
        "label_ko": detail["label_ko"],
        "emoji": detail["emoji"],
        "monthly_pts": plan["monthly_pts"],
        "coverage_pts": plan["coverage_pts"],
        "message": f"{detail['emoji']} {detail['label_ko']} 보험 가입 완료!",
        "legal_note": detail["legal_note"],
        "global_note": "Points-only — no currency exchange required.",
    }


@router.get("/my", response_model=List[PolicyOut], summary="내 보험 목록")
async def my_policies(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(InsurancePolicyV2).where(
            InsurancePolicyV2.user_id == current_user.id,
            InsurancePolicyV2.is_active == True,
        ).order_by(InsurancePolicyV2.start_date.desc())
    )
    policies = result.scalars().all()
    return [
        PolicyOut(
            id=p.id,
            tier=p.tier.value if hasattr(p.tier, "value") else p.tier,
            monthly_premium_pts=p.monthly_premium_pts,
            coverage_per_case_pts=p.coverage_per_case_pts,
            remaining_annual_pts=p.remaining_annual_pts,
            is_active=p.is_active,
            start_date=p.start_date,
            end_date=p.end_date,
            total_claims=p.total_claims,
            total_paid_pts=p.total_paid_pts,
        )
        for p in policies
    ]


@router.post("/cancel/{policy_id}", summary="보험 해지")
async def cancel(
    policy_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(InsurancePolicyV2).where(
            InsurancePolicyV2.id == policy_id,
            InsurancePolicyV2.user_id == current_user.id,
        )
    )
    policy = res.scalar_one_or_none()
    if not policy:
        raise HTTPException(404, "보험 정책을 찾을 수 없습니다.")

    policy.is_active = False
    policy.end_date = datetime.utcnow()
    policy.auto_renew = False
    await db.commit()
    return {"success": True, "message": "보험이 해지되었습니다.", "policy_id": policy_id}


@router.post("/claim", summary="보험 청구 (포인트 보상)")
async def create_claim(
    req: ClaimReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """보험금 청구 — 승인 시 포인트로 보상"""
    pol_res = await db.execute(
        select(InsurancePolicyV2).where(
            InsurancePolicyV2.id == req.policy_id,
            InsurancePolicyV2.user_id == current_user.id,
            InsurancePolicyV2.is_active == True,
        )
    )
    policy = pol_res.scalar_one_or_none()
    if not policy:
        raise HTTPException(404, "유효한 보험 정책 없음.")

    if req.claimed_points > policy.coverage_per_case_pts:
        raise HTTPException(400,
            f"청구 포인트가 보장 한도 초과. 한도: {policy.coverage_per_case_pts}P")

    if req.claimed_points > policy.remaining_annual_pts:
        raise HTTPException(400,
            f"연간 한도 초과. 잔여: {policy.remaining_annual_pts}P")

    claim = InsuranceClaimV2(
        id=str(uuid.uuid4()),
        policy_id=policy.id,
        claimant_id=current_user.id,
        bounty_id=req.bounty_id,
        answer_id=req.answer_id,
        claim_type=req.claim_type,
        description=req.description,
        claimed_points=req.claimed_points,
        evidence_urls=req.evidence_urls,
        status="pending",
    )
    db.add(claim)
    policy.total_claims += 1
    await db.commit()

    return {
        "success": True,
        "claim_id": claim.id,
        "claimed_points": req.claimed_points,
        "status": "pending",
        "message": "보험 청구 접수. 영업일 3일 이내 처리됩니다.",
        "process": [
            "1. 청구 내용 검토 (1~2 영업일)",
            "2. 승인 시 포인트 지급",
            "3. 거절 시 사유 통보",
        ],
        "note": "보상은 포인트로 지급됩니다. (현금 아님)"
    }


@router.get("/claims", summary="내 청구 목록")
async def my_claims(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(InsuranceClaimV2)
        .where(InsuranceClaimV2.claimant_id == current_user.id)
        .order_by(InsuranceClaimV2.created_at.desc())
        .limit(50)
    )
    claims = result.scalars().all()
    return [
        {
            "id": c.id,
            "claim_type": c.claim_type,
            "description": c.description,
            "claimed_points": c.claimed_points,
            "approved_points": c.approved_points,
            "status": c.status,
            "created_at": c.created_at.isoformat(),
            "processed_at": c.processed_at.isoformat() if c.processed_at else None,
        }
        for c in claims
    ]


@router.get("/pool/status", summary="보험 적립금 현황 (투명 공개)")
async def pool_status(db: AsyncSession = Depends(get_db)):
    """보험 포인트 풀 현황 — 투명성 공개"""
    res = await db.execute(
        select(InsurancePool).order_by(InsurancePool.date.desc()).limit(1)
    )
    pool = res.scalar_one_or_none()

    if not pool:
        return {
            "total_reserve_pts": 0,
            "monthly_premium_pts": 0,
            "monthly_claims_pts": 0,
            "reserve_ratio": 0.0,
            "is_solvent": True,
            "note": "보험 풀 초기화 대기 중",
        }

    monthly = pool.monthly_claims_paid or 1
    ratio = pool.total_reserve_pts / monthly

    return {
        "total_reserve_pts": pool.total_reserve_pts,
        "monthly_premium_pts": pool.monthly_premium_collected,
        "monthly_claims_pts": pool.monthly_claims_paid,
        "reserve_ratio": round(ratio, 2),
        "is_solvent": ratio >= 3.0,
        "transparency_note": "보험 적립금은 현상금 수수료 5%에서 자동 적립됩니다.",
    }
