"""
Dr. Vroom — 포인트 경제 스키마 (Points Economy Schema)
════════════════════════════════════════════════════════

설계 원칙:
  ① 현금 NO  → 법적으로 '앱 내 가상 재화' (게임머니와 동일 지위)
  ② 환율 없음 → 포인트는 통화가 아니므로 외환거래법 비적용
  ③ 금융업 등록 불필요 → 전자금융거래법 '선불전자지급수단' 해당 없음
                        (포인트가 '재화/서비스 대가'로만 사용되면 해당 없음)
  ④ VAT/세금 → 포인트 판매 시 이용약관에 명시 (부가세는 판매자 책임)
  ⑤ 글로벌 동일 적용 → USD/EUR/KRW 등 환율 계산 불필요

포인트 흐름:
  구매 (IAP)  → PointWallet.balance 증가
  진단 사용   → PointWallet.balance 감소
  기여 적립   → PointWallet.balance 증가
  현상금 예치 → PointEscrow (잠금)
  채택 지급   → PointEscrow 해제 → 전문가 PointWallet

수수료 (포인트 내부 공제):
  플랫폼 수수료 15% → PlatformPool
  보험 적립금  5%  → InsurancePool
  세금 없음        → 포인트는 통화가 아님
"""

from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Text,
    Boolean, JSON, ForeignKey, Enum as SAEnum, BigInteger
)
from datetime import datetime
import enum

from app.db.database import Base


# ══════════════════════════════════════════════════════════════
# ENUMS
# ══════════════════════════════════════════════════════════════

class PointTxType(str, enum.Enum):
    """포인트 거래 유형"""
    # 적립 (수입)
    PURCHASE          = "purchase"           # IAP 포인트 구매
    CONTRIBUTION      = "contribution"       # 소리 데이터 기여
    CONTRIBUTION_BONUS= "contribution_bonus" # 전문가 검증 보너스
    BOUNTY_REWARD     = "bounty_reward"      # 현상금 채택 보상
    DAILY_BONUS       = "daily_bonus"        # 출석 보너스
    REFERRAL          = "referral"           # 친구 초대
    REFUND            = "refund"             # 환불
    MEDIATION_WIN     = "mediation_win"      # 중재 승소

    # 차감 (지출)
    DIAGNOSIS_USE     = "diagnosis_use"      # 진단 1회 사용
    BOUNTY_POST       = "bounty_post"        # 현상금 게시 (예치)
    INSURANCE_PREMIUM = "insurance_premium"  # 보험 포인트 납부
    SUBSCRIPTION      = "subscription"       # 등급 구독
    PLATFORM_FEE      = "platform_fee"       # 플랫폼 수수료 (내부 공제)
    INSURANCE_FUND    = "insurance_fund"     # 보험 적립금 공제
    MEDIATION_PENALTY = "mediation_penalty"  # 중재 패소 페널티

class EscrowStatus(str, enum.Enum):
    HELD      = "held"
    RELEASED  = "released"
    REFUNDED  = "refunded"
    DISPUTED  = "disputed"

class BountyStatus(str, enum.Enum):
    OPEN      = "open"
    ANSWERED  = "answered"
    ADOPTED   = "adopted"
    EXPIRED   = "expired"
    DISPUTED  = "disputed"
    REFUNDED  = "refunded"

class ExpertTier(str, enum.Enum):
    APPRENTICE = "apprentice"   # 🔩 견습
    CERTIFIED  = "certified"    # 🔧 인증
    MASTER     = "master"       # 🏆 마스터
    PARTNER    = "partner"      # 👑 파트너

class UserTier(str, enum.Enum):
    FREE      = "free"          # 🌱 무료 (3회/월)
    STARTER   = "starter"       # 🚗 스타터 (구독 or 충전)
    PRO       = "pro"           # ⭐ 프로
    EXPERT    = "expert_user"   # 💎 전문가 (진단 무제한)

class InsuranceTier(str, enum.Enum):
    NONE      = "none"          # 미가입
    BASIC     = "basic"         # 🛡 기본 (자동 포함)
    STANDARD  = "standard"      # 🛡🛡 표준
    PREMIUM   = "premium"       # 💎🛡 프리미엄

class DisputeStatus(str, enum.Enum):
    PENDING   = "pending"
    REVIEWING = "reviewing"
    MEDIATION = "mediation"
    RESOLVED  = "resolved"
    CLOSED    = "closed"


# ══════════════════════════════════════════════════════════════
# 1. POINT WALLET — 포인트 지갑
# ══════════════════════════════════════════════════════════════

class PointWallet(Base):
    """
    사용자별 포인트 지갑
    balance = 사용 가능 잔액
    locked  = 에스크로 예치 중 (현상금 등)
    """
    __tablename__ = "point_wallets"

    user_id          = Column(String, ForeignKey("users.id"), primary_key=True)
    balance          = Column(BigInteger, default=0)      # 사용 가능 포인트
    locked           = Column(BigInteger, default=0)      # 에스크로 잠금 포인트
    lifetime_earned  = Column(BigInteger, default=0)      # 누적 적립
    lifetime_spent   = Column(BigInteger, default=0)      # 누적 사용

    # 이번 달 통계
    monthly_earned   = Column(BigInteger, default=0)
    monthly_spent    = Column(BigInteger, default=0)
    month_reset_at   = Column(DateTime, default=datetime.utcnow)

    updated_at       = Column(DateTime, default=datetime.utcnow)


class PointTransaction(Base):
    """
    포인트 거래 내역 — 완전한 회계 장부
    모든 증감 내역 기록 (취소 불가)
    """
    __tablename__ = "point_transactions"

    id               = Column(String, primary_key=True)
    user_id          = Column(String, ForeignKey("users.id"), index=True)
    tx_type          = Column(SAEnum(PointTxType), index=True)

    amount           = Column(BigInteger)        # 양수=적립, 음수=차감
    balance_after    = Column(BigInteger)        # 거래 후 잔액
    locked_after     = Column(BigInteger, default=0)  # 거래 후 잠금 잔액

    # 참조
    ref_id           = Column(String, index=True)   # bounty_id / escrow_id 등
    ref_type         = Column(String)               # "bounty"/"diagnosis" 등
    description      = Column(Text)

    # 비고 (법적 기록)
    note             = Column(Text, nullable=True)  # "앱 내 가상 재화 거래"

    created_at       = Column(DateTime, default=datetime.utcnow, index=True)


# ══════════════════════════════════════════════════════════════
# 2. POINT ESCROW — 현상금 안전 잠금
# ══════════════════════════════════════════════════════════════

class PointEscrow(Base):
    """
    현상금 포인트 에스크로
    - 현상금 게시 시 사용자 지갑에서 잠금
    - 채택 시 → 수수료 공제 후 전문가 지갑으로
    - 만료/분쟁 패소 시 → 사용자에게 환불
    
    수수료 (포인트 내부):
      플랫폼: 15%
      보험 적립: 5%
      전문가 실수령: 80%
      * 포인트는 가상재화이므로 원천징수세 없음
    """
    __tablename__ = "point_escrows"

    id                   = Column(String, primary_key=True)
    bounty_id            = Column(String, ForeignKey("bounties_v2.id"), unique=True)
    poster_id            = Column(String, ForeignKey("users.id"))
    expert_id            = Column(String, nullable=True)

    total_points         = Column(BigInteger)           # 총 현상금
    platform_fee_pts     = Column(BigInteger, default=0) # 15%
    insurance_fund_pts   = Column(BigInteger, default=0) # 5%
    expert_net_pts       = Column(BigInteger, default=0) # 80%

    status               = Column(SAEnum(EscrowStatus), default=EscrowStatus.HELD)
    held_at              = Column(DateTime, default=datetime.utcnow)
    release_after        = Column(DateTime, nullable=True)   # 7일 후 자동 확정
    released_at          = Column(DateTime, nullable=True)
    dispute_deadline     = Column(DateTime, nullable=True)


# ══════════════════════════════════════════════════════════════
# 3. BOUNTY V2 — 현상금 게시판 (포인트 전용)
# ══════════════════════════════════════════════════════════════

class BountyV2(Base):
    """
    현상금 게시글 (포인트 전용)
    현금 필드 제거, 포인트만 사용
    """
    __tablename__ = "bounties_v2"

    id               = Column(String, primary_key=True)
    user_id          = Column(String, ForeignKey("users.id"), index=True)

    title            = Column(String)
    description      = Column(Text)

    # 차량 정보
    vehicle_type     = Column(String)
    vehicle_brand    = Column(String, index=True)
    vehicle_model    = Column(String)
    vehicle_year     = Column(Integer)
    mileage          = Column(Integer, nullable=True)

    # 소리/증상
    sound_session_id = Column(String, nullable=True)
    sound_tags       = Column(JSON, default=list)
    symptom_when     = Column(String)
    symptom_location = Column(String)

    # 현상금 (포인트 전용)
    reward_points    = Column(BigInteger, default=100)
    min_expert_tier  = Column(SAEnum(ExpertTier), default=ExpertTier.APPRENTICE)

    # 상태
    status           = Column(SAEnum(BountyStatus), default=BountyStatus.OPEN, index=True)
    adopted_answer_id= Column(String, nullable=True)
    adopted_at       = Column(DateTime, nullable=True)
    expires_at       = Column(DateTime)
    created_at       = Column(DateTime, default=datetime.utcnow)

    view_count       = Column(Integer, default=0)
    answer_count     = Column(Integer, default=0)


class BountyAnswerV2(Base):
    """전문가 답변"""
    __tablename__ = "bounty_answers_v2"

    id               = Column(String, primary_key=True)
    bounty_id        = Column(String, ForeignKey("bounties_v2.id"), index=True)
    expert_id        = Column(String, ForeignKey("users.id"), index=True)

    diagnosis        = Column(Text)
    likely_cause     = Column(Text)
    repair_method    = Column(Text)
    estimated_cost   = Column(String)
    urgency          = Column(String)
    confidence       = Column(Float)
    fault_code       = Column(String, nullable=True)
    freq_analysis    = Column(JSON, nullable=True)

    is_adopted       = Column(Boolean, default=False)
    rating           = Column(Integer, nullable=True)
    rating_comment   = Column(Text, nullable=True)
    helpful_count    = Column(Integer, default=0)

    created_at       = Column(DateTime, default=datetime.utcnow)
    adopted_at       = Column(DateTime, nullable=True)


# ══════════════════════════════════════════════════════════════
# 4. USER GRADE V2 — 등급 (구독 = 포인트 차감)
# ══════════════════════════════════════════════════════════════

class UserGradeV2(Base):
    """
    사용자 등급 (포인트 기반 구독)
    월 구독료 = 포인트 차감 (현금 없음)
    """
    __tablename__ = "user_grades_v2"

    user_id              = Column(String, ForeignKey("users.id"), primary_key=True)
    tier                 = Column(SAEnum(UserTier), default=UserTier.FREE)
    tier_since           = Column(DateTime, default=datetime.utcnow)
    tier_expires         = Column(DateTime, nullable=True)   # 포인트 구독 만료일
    auto_renew           = Column(Boolean, default=False)

    # 이번 달 진단 횟수
    monthly_diagnoses    = Column(Integer, default=0)
    month_reset_at       = Column(DateTime, default=datetime.utcnow)
    total_diagnoses      = Column(Integer, default=0)

    # 기여 통계
    sound_contributions  = Column(Integer, default=0)
    verified_sounds      = Column(Integer, default=0)
    total_bounties_posted= Column(Integer, default=0)
    reputation_score     = Column(Float, default=0.0)   # 0~100

    updated_at           = Column(DateTime, default=datetime.utcnow)


class ExpertProfileV2(Base):
    """
    전문가 프로필 v2
    수익 = 포인트 (현금 정산 없음, 법적 단순화)
    """
    __tablename__ = "expert_profiles_v2"

    user_id              = Column(String, ForeignKey("users.id"), primary_key=True)
    tier                 = Column(SAEnum(ExpertTier), default=ExpertTier.APPRENTICE)
    tier_since           = Column(DateTime, default=datetime.utcnow)

    # 자격증 (선택 — 없어도 견습으로 활동 가능)
    license_type         = Column(String, nullable=True)
    license_number       = Column(String, nullable=True)
    license_verified     = Column(Boolean, default=False)

    # 전문 분야
    specialties          = Column(JSON, default=list)
    vehicle_brands       = Column(JSON, default=list)
    experience_years     = Column(Integer, default=0)
    country              = Column(String, default="KR")  # ISO 국가코드 (글로벌)

    # 성과
    total_answers        = Column(Integer, default=0)
    adopted_answers      = Column(Integer, default=0)
    adoption_rate        = Column(Float, default=0.0)
    avg_rating           = Column(Float, default=0.0)
    total_ratings        = Column(Integer, default=0)
    total_earned_points  = Column(BigInteger, default=0)  # 누적 획득 포인트

    is_active            = Column(Boolean, default=True)
    is_suspended         = Column(Boolean, default=False)

    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow)


# ══════════════════════════════════════════════════════════════
# 5. INSURANCE V2 — 보험 (포인트 납부)
# ══════════════════════════════════════════════════════════════

class InsurancePolicyV2(Base):
    """
    보험 정책 (포인트로 납부)
    보상도 포인트로 지급 → 법적으로 '가상재화 교환 프로그램'
    """
    __tablename__ = "insurance_policies_v2"

    id                   = Column(String, primary_key=True)
    user_id              = Column(String, ForeignKey("users.id"), index=True)
    tier                 = Column(SAEnum(InsuranceTier), default=InsuranceTier.BASIC)

    monthly_premium_pts  = Column(Integer, default=0)   # 월 납부 포인트
    coverage_per_case_pts= Column(Integer, default=0)   # 건당 보상 포인트
    annual_limit_pts     = Column(Integer, default=0)   # 연간 한도 포인트

    is_active            = Column(Boolean, default=True)
    start_date           = Column(DateTime, default=datetime.utcnow)
    end_date             = Column(DateTime, nullable=True)
    auto_renew           = Column(Boolean, default=True)

    total_claims         = Column(Integer, default=0)
    total_paid_pts       = Column(Integer, default=0)
    remaining_annual_pts = Column(Integer, default=0)

    created_at           = Column(DateTime, default=datetime.utcnow)


class InsuranceClaimV2(Base):
    """보험 청구 (포인트 보상)"""
    __tablename__ = "insurance_claims_v2"

    id                   = Column(String, primary_key=True)
    policy_id            = Column(String, ForeignKey("insurance_policies_v2.id"))
    claimant_id          = Column(String, ForeignKey("users.id"), index=True)
    bounty_id            = Column(String, nullable=True)
    answer_id            = Column(String, nullable=True)

    claim_type           = Column(String)  # "wrong_diagnosis"/"expert_liability"
    description          = Column(Text)
    claimed_points       = Column(Integer)
    approved_points      = Column(Integer, default=0)
    evidence_urls        = Column(JSON, default=list)

    status               = Column(String, default="pending")
    rejection_reason     = Column(Text, nullable=True)
    processed_at         = Column(DateTime, nullable=True)

    created_at           = Column(DateTime, default=datetime.utcnow)


class InsurancePool(Base):
    """
    보험 적립금 풀 (포인트)
    현상금 공제 5% 자동 적립
    """
    __tablename__ = "insurance_pool_v2"

    id                        = Column(Integer, primary_key=True, autoincrement=True)
    date                      = Column(DateTime, default=datetime.utcnow)
    total_reserve_pts         = Column(BigInteger, default=0)
    monthly_premium_collected = Column(BigInteger, default=0)
    monthly_claims_paid       = Column(BigInteger, default=0)
    monthly_fee_contribution  = Column(BigInteger, default=0)
    reserve_ratio             = Column(Float, default=0.0)


# ══════════════════════════════════════════════════════════════
# 6. DISPUTE V2 — 분쟁 중재
# ══════════════════════════════════════════════════════════════

class DisputeV2(Base):
    """분쟁 처리"""
    __tablename__ = "disputes_v2"

    id               = Column(String, primary_key=True)
    bounty_id        = Column(String, ForeignKey("bounties_v2.id"), index=True)
    answer_id        = Column(String)
    complainant_id   = Column(String, ForeignKey("users.id"))
    respondent_id    = Column(String, ForeignKey("users.id"))

    reason           = Column(Text)
    evidence_urls    = Column(JSON, default=list)
    claimed_refund_pts = Column(BigInteger, default=0)

    status           = Column(SAEnum(DisputeStatus), default=DisputeStatus.PENDING)
    mediator_ids     = Column(JSON, default=list)
    verdict          = Column(String, nullable=True)
    verdict_reason   = Column(Text, nullable=True)
    refund_pts       = Column(BigInteger, default=0)
    penalty_pts      = Column(BigInteger, default=0)

    created_at       = Column(DateTime, default=datetime.utcnow)
    resolved_at      = Column(DateTime, nullable=True)
    deadline         = Column(DateTime)


class MediationVoteV2(Base):
    """중재자 투표"""
    __tablename__ = "mediation_votes_v2"

    id               = Column(String, primary_key=True)
    dispute_id       = Column(String, ForeignKey("disputes_v2.id"), index=True)
    mediator_id      = Column(String, ForeignKey("users.id"))
    vote             = Column(String)
    opinion          = Column(Text)
    voted_at         = Column(DateTime, default=datetime.utcnow)


# ══════════════════════════════════════════════════════════════
# 7. SOUND CONTRIBUTION — 소리 기여 (포인트 적립)
# ══════════════════════════════════════════════════════════════

class SoundContribution(Base):
    """소리 데이터 기여 → 포인트 자동 적립"""
    __tablename__ = "sound_contributions_v2"

    id                   = Column(String, primary_key=True)
    contributor_id       = Column(String, ForeignKey("users.id"), index=True)

    vehicle_brand        = Column(String)
    vehicle_model        = Column(String)
    vehicle_year         = Column(Integer)
    mileage              = Column(Integer, nullable=True)
    engine_cc            = Column(Integer, nullable=True)
    country              = Column(String, default="KR")   # 글로벌 데이터

    sound_type           = Column(String)   # "normal"/"fault"
    component            = Column(String)
    fault_code           = Column(String, nullable=True)
    description          = Column(Text)
    sound_session_id     = Column(String)

    # 검증
    verified_by          = Column(String, nullable=True)
    verification_status  = Column(String, default="pending")
    verified_at          = Column(DateTime, nullable=True)

    # 포인트 보상
    points_awarded       = Column(Integer, default=10)   # 제출 시 10P
    bonus_points         = Column(Integer, default=0)    # 검증 통과 시 +40P

    created_at           = Column(DateTime, default=datetime.utcnow)


# ══════════════════════════════════════════════════════════════
# 8. GRADE HISTORY
# ══════════════════════════════════════════════════════════════

class GradeHistoryV2(Base):
    """등급 변경 이력"""
    __tablename__ = "grade_histories_v2"

    id           = Column(String, primary_key=True)
    user_id      = Column(String, ForeignKey("users.id"), index=True)
    grade_type   = Column(String)   # "user" / "expert"
    from_tier    = Column(String)
    to_tier      = Column(String)
    reason       = Column(Text)
    points_cost  = Column(Integer, default=0)   # 구독에 사용된 포인트
    changed_at   = Column(DateTime, default=datetime.utcnow)
    changed_by   = Column(String, default="system")
