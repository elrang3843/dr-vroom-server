"""
Dr. Vroom — 보험 시스템 API
Insurance System API

보험 종류:
  기본(무료)  — 건당 5만원 한도
  표준(490원) — 건당 20만원 한도
  프리미엄    — 건당 50만원 한도
  전문가 배상  — 건당 200만원 한도

법적 고지:
  - 이 보험은 상호부조 형태의 플랫폼 내부 보상 제도입니다.
  - 금융감독원 등록 보험 상품이 아닙니다.
  - 법적 분쟁 발생 시 민사 소송으로 해결합니다.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import uuid

from app.db.database import get_db, User
from app.db.ecosystem_schema import (
    InsurancePolicy, InsuranceClaim, InsurancePool,
    InsuranceType
)
from app.core.auth import get_current_user

router = APIRouter(prefix="/api/v1/insurance", tags=["insurance"])


# ── Insurance plan definitions ────────────────────────────────
INSURANCE_PLANS = {
    InsuranceType.BASIC: {
        "ko": "기본 보험",
        "emoji": "🛡️",
        "monthly_premium": 0,
        "coverage_per_case": 50000,
        "annual_limit": 100000,
        "deductible": 10000,
        "description": "무료 기본 보험 — 건당 최대 5만원 보상",
        "covers": [
            "AI 오진단으로 인한 불필요한 수리비 (5만원 한도)",
        ],
        "legal_note": "플랫폼 내부 보상 제도이며 금융 상품이 아닙니다."
    },
    InsuranceType.STANDARD: {
        "ko": "표준 보험",
        "emoji": "🛡️🛡️",
        "monthly_premium": 490,
        "coverage_per_case": 200000,
        "annual_limit": 600000,
        "deductible": 20000,
        "description": "월 490원 — 건당 최대 20만원 보상",
        "covers": [
            "AI 오진단으로 인한 수리비 (20만원 한도)",
            "전문가 오답변 배상 청구 지원",
        ],
        "legal_note": "플랫폼 내부 보상 제도이며 금융 상품이 아닙니다."
    },
    InsuranceType.PREMIUM: {
        "ko": "프리미엄 보험",
        "emoji": "💎🛡️",
        "monthly_premium": 990,
        "coverage_per_case": 500000,
        "annual_limit": 2000000,
        "deductible": 50000,
        "description": "월 990원 — 건당 최대 50만원 보상",
        "covers": [
            "AI/전문가 오진단 수리비 (50만원 한도)",
            "진단 지연으로 인한 추가 손해",
            "긴급 출동 서비스 연계",
        ],
        "legal_note": "플랫폼 내부 보상 제도이며 금융 상품이 아닙니다."
    },
    InsuranceType.EXPERT: {
        "ko": "전문가 배상 보험",
        "emoji": "👔🛡️",
        "monthly_premium": 2900,
        "coverage_per_case": 2000000,
        "annual_limit": 10000000,
        "deductible": 100000,
        "description": "월 2,900원 — 전문가 전용, 건당 최대 200만원 배상 보호",
        "covers": [
            "잘못된 진단 답변으로 인한 배상 책임 (200만원 한도)",
            "사용자 분쟁 법적 대응 지원",
            "전문가 신용 보호",
        ],
        "legal_note": "전문가 전용 배상 책임 보호 제도입니다. 고의·중과실은 면책 제외."
    },
}


# ── Pydantic Schemas ───────────────────────────────────────────

class PolicyResponse(BaseModel):
    id: str
    policy_type: str
    policy_type_ko: str
    emoji: str
    monthly_premium: int
    coverage_per_case: int
    annual_limit: int
    deductible: int
    is_active: bool
    start_date: datetime
    end_date: Optional[datetime]
    total_claims: int
    total_paid: int
    remaining_annual: int


class PolicyEnrollRequest(BaseModel):
    policy_type: str
    auto_renew: bool = True


class ClaimCreateRequest(BaseModel):
    policy_id: str
    bounty_id: Optional[str] = None
    answer_id: Optional[str] = None
    claim_type: str      # "wrong_diagnosis" / "expert_liability"
    description: str
    claimed_amount: int
    evidence_urls: List[str] = []


class ClaimResponse(BaseModel):
    id: str
    claim_type: str
    description: str
    claimed_amount: int
    approved_amount: int
    status: str
    created_at: datetime
    processed_at: Optional[datetime]
    rejection_reason: Optional[str]


class InsurancePoolStatus(BaseModel):
    total_reserve: int
    monthly_premium_collected: int
    monthly_claims_paid: int
    reserve_ratio: float
    is_solvent: bool


# ── Endpoints ──────────────────────────────────────────────────

@router.get("/plans", summary="보험 상품 목록")
async def get_insurance_plans():
    """가입 가능한 보험 상품 전체 목록 및 상세 설명"""
    return {
        "plans": [
            {
                "type": ptype.value,
                **plan,
            }
            for ptype, plan in INSURANCE_PLANS.items()
        ],
        "legal_disclaimer": (
            "닥터브릉이 보험 제도는 플랫폼 내부 상호부조 보상 프로그램으로, "
            "보험업법에 따른 금융감독원 등록 보험 상품이 아닙니다. "
            "보상은 플랫폼 운영 정책에 따라 지급되며, "
            "분쟁 발생 시 약관에 명시된 중재 절차를 따릅니다."
        )
    }


@router.get("/my", response_model=List[PolicyResponse], summary="내 보험 조회")
async def get_my_policies(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """현재 가입된 보험 정책 목록"""
    result = await db.execute(
        select(InsurancePolicy)
        .where(InsurancePolicy.user_id == current_user.id, InsurancePolicy.is_active == True)
        .order_by(InsurancePolicy.start_date.desc())
    )
    policies = result.scalars().all()

    out = []
    for p in policies:
        plan = INSURANCE_PLANS.get(p.policy_type, {})
        out.append(PolicyResponse(
            id=p.id,
            policy_type=p.policy_type.value if hasattr(p.policy_type, 'value') else p.policy_type,
            policy_type_ko=plan.get("ko", ""),
            emoji=plan.get("emoji", ""),
            monthly_premium=p.monthly_premium,
            coverage_per_case=p.coverage_per_case,
            annual_limit=plan.get("annual_limit", 0),
            deductible=p.deductible,
            is_active=p.is_active,
            start_date=p.start_date,
            end_date=p.end_date,
            total_claims=p.total_claims,
            total_paid=p.total_paid,
            remaining_annual=p.remaining_annual,
        ))
    return out


@router.post("/enroll", summary="보험 가입")
async def enroll_insurance(
    req: PolicyEnrollRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """보험 가입 처리 (결제 연동 전 시뮬레이션)"""
    try:
        ins_type = InsuranceType(req.policy_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 보험 종류: {req.policy_type}")

    plan = INSURANCE_PLANS[ins_type]

    # Check duplicate
    existing = await db.execute(
        select(InsurancePolicy).where(
            InsurancePolicy.user_id == current_user.id,
            InsurancePolicy.policy_type == ins_type,
            InsurancePolicy.is_active == True,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="이미 동일한 보험에 가입되어 있습니다.")

    policy = InsurancePolicy(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        policy_type=ins_type,
        coverage_per_case=plan["coverage_per_case"],
        monthly_premium=plan["monthly_premium"],
        deductible=plan["deductible"],
        is_active=True,
        start_date=datetime.utcnow(),
        end_date=datetime.utcnow() + timedelta(days=365),
        auto_renew=req.auto_renew,
        remaining_annual=plan["annual_limit"],
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)

    return {
        "success": True,
        "policy_id": policy.id,
        "policy_type": ins_type.value,
        "policy_type_ko": plan["ko"],
        "emoji": plan["emoji"],
        "monthly_premium": plan["monthly_premium"],
        "coverage_per_case": plan["coverage_per_case"],
        "message": f"{plan['emoji']} {plan['ko']} 가입 완료!",
        "legal_note": plan["legal_note"],
        "note": "실제 결제 연동 전 테스트 모드입니다."
    }


@router.post("/cancel/{policy_id}", summary="보험 해지")
async def cancel_insurance(
    policy_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """보험 해지 처리"""
    result = await db.execute(
        select(InsurancePolicy).where(
            InsurancePolicy.id == policy_id,
            InsurancePolicy.user_id == current_user.id,
        )
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="보험 정책을 찾을 수 없습니다.")

    policy.is_active = False
    policy.end_date = datetime.utcnow()
    policy.auto_renew = False
    await db.commit()

    return {"success": True, "message": "보험이 해지되었습니다.", "policy_id": policy_id}


@router.post("/claim", summary="보험 청구")
async def create_claim(
    req: ClaimCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """보험금 청구 신청"""
    # Verify policy
    policy_res = await db.execute(
        select(InsurancePolicy).where(
            InsurancePolicy.id == req.policy_id,
            InsurancePolicy.user_id == current_user.id,
            InsurancePolicy.is_active == True,
        )
    )
    policy = policy_res.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="유효한 보험 정책을 찾을 수 없습니다.")

    if req.claimed_amount > policy.coverage_per_case:
        raise HTTPException(
            status_code=400,
            detail=f"청구 금액이 보장 한도를 초과합니다. 한도: {policy.coverage_per_case:,}원"
        )

    if req.claimed_amount > policy.remaining_annual:
        raise HTTPException(
            status_code=400,
            detail=f"연간 한도를 초과합니다. 잔여 한도: {policy.remaining_annual:,}원"
        )

    claim = InsuranceClaim(
        id=str(uuid.uuid4()),
        policy_id=policy.id,
        claimant_id=current_user.id,
        bounty_id=req.bounty_id,
        answer_id=req.answer_id,
        claim_type=req.claim_type,
        description=req.description,
        claimed_amount=req.claimed_amount,
        evidence_urls=req.evidence_urls,
        status="pending",
    )
    db.add(claim)
    policy.total_claims += 1
    await db.commit()

    return {
        "success": True,
        "claim_id": claim.id,
        "claimed_amount": req.claimed_amount,
        "status": "pending",
        "message": "보험 청구가 접수되었습니다. 영업일 3일 이내에 처리됩니다.",
        "process_note": [
            "1. 청구 내용 및 증빙 검토 (1~2 영업일)",
            "2. 원인 분석 및 승인 여부 결정",
            "3. 승인 시 5 영업일 내 지급",
        ]
    }


@router.get("/claims", response_model=List[ClaimResponse], summary="내 청구 목록")
async def get_my_claims(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """내 보험 청구 목록 조회"""
    result = await db.execute(
        select(InsuranceClaim)
        .where(InsuranceClaim.claimant_id == current_user.id)
        .order_by(InsuranceClaim.created_at.desc())
        .limit(50)
    )
    claims = result.scalars().all()
    return [
        ClaimResponse(
            id=c.id,
            claim_type=c.claim_type,
            description=c.description,
            claimed_amount=c.claimed_amount,
            approved_amount=c.approved_amount,
            status=c.status,
            created_at=c.created_at,
            processed_at=c.processed_at,
            rejection_reason=c.rejection_reason,
        )
        for c in claims
    ]


@router.get("/pool/status", response_model=InsurancePoolStatus, summary="보험 적립금 현황")
async def get_insurance_pool_status(db: AsyncSession = Depends(get_db)):
    """보험 적립금 풀 현황 (투명성 공개)"""
    result = await db.execute(
        select(InsurancePool).order_by(InsurancePool.date.desc()).limit(1)
    )
    pool = result.scalar_one_or_none()

    if not pool:
        # Return empty state
        return InsurancePoolStatus(
            total_reserve=0,
            monthly_premium_collected=0,
            monthly_claims_paid=0,
            reserve_ratio=0.0,
            is_solvent=True,
        )

    monthly_exposure = pool.monthly_claims_paid or 1
    reserve_ratio = pool.total_reserve / monthly_exposure if monthly_exposure > 0 else 99.0

    return InsurancePoolStatus(
        total_reserve=pool.total_reserve,
        monthly_premium_collected=pool.monthly_premium_collected,
        monthly_claims_paid=pool.monthly_claims_paid,
        reserve_ratio=round(reserve_ratio, 2),
        is_solvent=reserve_ratio >= 3.0,   # 3개월치 준비금 = 건전
    )
