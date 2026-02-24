"""
닥터브릉이 생태계 완전 DB 스키마
Dr. Vroom Ecosystem — Complete Database Schema

┌─────────────────────────────────────────────────────────────────┐
│  등급 시스템    보상 시스템    보험 시스템    분쟁 시스템         │
│  UserGrade      Bounty         Insurance      Dispute            │
│  ExpertGrade    Escrow         InsuranceClaim MediationVote      │
│  GradeHistory   Reward         InsurancePool                     │
│                 Transaction                                      │
└─────────────────────────────────────────────────────────────────┘
"""

from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Text,
    Boolean, JSON, ForeignKey, Enum as SAEnum, BigInteger
)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.db.database import Base


# ═══════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════

class UserTier(str, enum.Enum):
    """사용자 등급"""
    SPROUT   = "sprout"    # 🌱 새싹 — 무료 3회/월
    DRIVER   = "driver"    # 🚗 드라이버 — 월 990원, 30회/월
    GOLD     = "gold"      # ⭐ 골드 — 월 2,990원, 무제한
    DIAMOND  = "diamond"   # 💎 다이아 — 월 9,900원, 무제한 + 우선 처리

class ExpertTier(str, enum.Enum):
    """전문가 등급"""
    APPRENTICE = "apprentice"  # 🔩 견습 — 인증 대기/기초
    CERTIFIED  = "certified"   # 🔧 인증 정비사 — 자격증 확인
    MASTER     = "master"      # 🏆 마스터 — 500건+ 채택, 평점 4.5+
    PARTNER    = "partner"     # 👑 닥터브릉이 파트너 — 최상위

class BountyStatus(str, enum.Enum):
    """현상금 상태"""
    OPEN      = "open"       # 답변 모집 중
    ANSWERED  = "answered"   # 답변 있음, 채택 대기
    ADOPTED   = "adopted"    # 채택 완료, 보상 지급
    EXPIRED   = "expired"    # 만료 (7일)
    DISPUTED  = "disputed"   # 분쟁 중
    REFUNDED  = "refunded"   # 환불 처리

class EscrowStatus(str, enum.Enum):
    """에스크로 상태"""
    HELD      = "held"       # 보관 중
    RELEASED  = "released"   # 전문가에게 지급
    REFUNDED  = "refunded"   # 사용자에게 환불
    DISPUTED  = "disputed"   # 분쟁으로 동결

class InsuranceType(str, enum.Enum):
    """보험 종류"""
    BASIC     = "basic"      # 기본 — 무료, 건당 최대 5만원
    STANDARD  = "standard"   # 표준 — 월 490원, 건당 최대 20만원
    PREMIUM   = "premium"    # 프리미엄 — 월 990원, 건당 최대 50만원
    EXPERT    = "expert"     # 전문가 배상 — 월 2,900원, 건당 최대 200만원

class TransactionType(str, enum.Enum):
    """거래 유형"""
    BOUNTY_DEPOSIT   = "bounty_deposit"   # 현상금 예치
    REWARD_PAYMENT   = "reward_payment"   # 보상 지급
    PLATFORM_FEE     = "platform_fee"     # 플랫폼 수수료 (15%)
    INSURANCE_FEE    = "insurance_fee"    # 보험료 (5%)
    TAX_WITHHOLD     = "tax_withhold"     # 원천징수 (3.3%)
    SUBSCRIPTION     = "subscription"    # 구독료
    REFUND           = "refund"           # 환불
    POINT_CONVERT    = "point_convert"    # 포인트 전환

class DisputeStatus(str, enum.Enum):
    """분쟁 상태"""
    PENDING   = "pending"    # 접수
    REVIEWING = "reviewing"  # 검토 중
    MEDIATION = "mediation"  # 중재 중
    RESOLVED  = "resolved"   # 해결
    CLOSED    = "closed"     # 종료


# ═══════════════════════════════════════════════════════════════
# 1. 등급 시스템
# ═══════════════════════════════════════════════════════════════

class UserGrade(Base):
    """
    사용자 등급 정보
    등급에 따라 월 진단 횟수, 현상금 한도, 보험 가입 가능 여부 결정
    """
    __tablename__ = "user_grades"

    user_id          = Column(String, ForeignKey("users.id"), primary_key=True)

    # 현재 등급
    tier             = Column(SAEnum(UserTier), default=UserTier.SPROUT, index=True)
    tier_since       = Column(DateTime, default=datetime.utcnow)

    # 구독 정보
    subscription_active   = Column(Boolean, default=False)
    subscription_expires  = Column(DateTime, nullable=True)
    auto_renew            = Column(Boolean, default=False)

    # 이용 통계
    monthly_diagnoses     = Column(Integer, default=0)   # 이번 달 진단 횟수
    total_diagnoses       = Column(Integer, default=0)   # 누적 진단 횟수
    total_bounties_posted = Column(Integer, default=0)   # 누적 현상금 게시
    total_spent_points    = Column(Integer, default=0)   # 누적 사용 포인트

    # 포인트 (기여 보상으로 적립)
    points               = Column(Integer, default=0)
    lifetime_points      = Column(Integer, default=0)

    # 평판
    reputation_score     = Column(Float, default=0.0)    # 0~100
    sound_contributions  = Column(Integer, default=0)    # 제공한 소리 데이터 수
    verified_sounds      = Column(Integer, default=0)    # 전문가 검증 통과 수

    updated_at           = Column(DateTime, default=datetime.utcnow)


class ExpertProfile(Base):
    """
    전문가 프로필 및 등급
    자격증 인증 → 견습 → 인증 → 마스터 → 파트너
    """
    __tablename__ = "expert_profiles"

    user_id              = Column(String, ForeignKey("users.id"), primary_key=True)

    # 등급
    tier                 = Column(SAEnum(ExpertTier), default=ExpertTier.APPRENTICE)
    tier_since           = Column(DateTime, default=datetime.utcnow)
    tier_expires         = Column(DateTime, nullable=True)  # 연간 갱신

    # 자격증 정보
    license_type         = Column(String)        # 자동차정비기능사, 산업기사, 기사
    license_number       = Column(String)        # 자격증 번호
    license_verified     = Column(Boolean, default=False)
    license_doc_url      = Column(String)        # 자격증 사진 URL

    # 사업자 정보 (선택)
    business_name        = Column(String)        # 정비소 이름
    business_reg_number  = Column(String)        # 사업자등록번호
    business_verified    = Column(Boolean, default=False)
    business_address     = Column(String)

    # 전문 분야
    specialties          = Column(JSON, default=list)  # ["engine","transmission"]
    vehicle_brands       = Column(JSON, default=list)  # ["hyundai","kia","bmw"]
    experience_years     = Column(Integer, default=0)

    # 성과 지표
    total_answers        = Column(Integer, default=0)
    adopted_answers      = Column(Integer, default=0)
    adoption_rate        = Column(Float, default=0.0)   # 채택률
    avg_rating           = Column(Float, default=0.0)   # 평균 평점 (1~5)
    total_ratings        = Column(Integer, default=0)
    total_earned_points  = Column(Integer, default=0)   # 누적 획득 포인트
    total_earned_krw     = Column(Integer, default=0)   # 누적 수익 (원)

    # 이번 달 통계
    monthly_answers      = Column(Integer, default=0)
    monthly_adopted      = Column(Integer, default=0)
    monthly_earned       = Column(Integer, default=0)

    # 계정 상태
    is_active            = Column(Boolean, default=True)
    is_suspended         = Column(Boolean, default=False)
    suspension_reason    = Column(Text, nullable=True)

    # 정산 정보
    bank_name            = Column(String)        # 은행명
    bank_account         = Column(String)        # 계좌번호 (암호화 저장)
    account_holder       = Column(String)        # 예금주
    settlement_day       = Column(Integer, default=25)  # 정산일 (매월 25일)

    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow)


class GradeHistory(Base):
    """등급 변경 이력"""
    __tablename__ = "grade_histories"

    id           = Column(String, primary_key=True)
    user_id      = Column(String, ForeignKey("users.id"), index=True)
    grade_type   = Column(String)              # "user" or "expert"
    from_tier    = Column(String)
    to_tier      = Column(String)
    reason       = Column(Text)
    changed_at   = Column(DateTime, default=datetime.utcnow)
    changed_by   = Column(String)              # "system" or admin user_id


# ═══════════════════════════════════════════════════════════════
# 2. 현상금 & 보상 시스템
# ═══════════════════════════════════════════════════════════════

class Bounty(Base):
    """
    현상금 게시판
    사용자가 차량 문제를 올리고 전문가의 답변에 보상을 걺
    """
    __tablename__ = "bounties"

    id               = Column(String, primary_key=True)
    user_id          = Column(String, ForeignKey("users.id"), index=True)

    # 문제 설명
    title            = Column(String)
    description      = Column(Text)
    vehicle_type     = Column(String)      # sedan/suv/truck
    vehicle_brand    = Column(String)      # hyundai/kia/bmw
    vehicle_model    = Column(String)      # sonata/k5
    vehicle_year     = Column(Integer)
    mileage          = Column(Integer)     # km

    # 소리 데이터
    sound_session_id = Column(String)      # 진단 세션 ID (소리 데이터 연결)
    sound_tags       = Column(JSON, default=list)  # ["knock","rattle","squeal"]
    symptom_when     = Column(String)      # "startup/acceleration/braking/always"
    symptom_location = Column(String)      # "front/rear/engine/wheel"

    # 현상금 설정
    reward_points    = Column(Integer, default=0)   # 포인트 현상금
    reward_krw       = Column(Integer, default=0)   # 원화 현상금 (0이면 포인트만)
    min_expert_tier  = Column(SAEnum(ExpertTier), default=ExpertTier.APPRENTICE)

    # 상태
    status           = Column(SAEnum(BountyStatus), default=BountyStatus.OPEN, index=True)
    adopted_answer_id = Column(String, nullable=True)

    # 시간
    expires_at       = Column(DateTime)    # 7일 후 만료
    adopted_at       = Column(DateTime, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)

    # 통계
    view_count       = Column(Integer, default=0)
    answer_count     = Column(Integer, default=0)


class BountyAnswer(Base):
    """
    전문가 답변
    현상금 게시글에 대한 전문가의 진단 및 해결책
    """
    __tablename__ = "bounty_answers"

    id               = Column(String, primary_key=True)
    bounty_id        = Column(String, ForeignKey("bounties.id"), index=True)
    expert_id        = Column(String, ForeignKey("users.id"), index=True)

    # 답변 내용
    diagnosis        = Column(Text)        # 진단 결과
    likely_cause     = Column(Text)        # 예상 원인
    repair_method    = Column(Text)        # 수리 방법
    estimated_cost   = Column(String)      # 예상 수리비 (범위: "5만~10만원")
    urgency          = Column(String)      # "즉시수리/주의관찰/정기점검"
    confidence       = Column(Float)       # 확신도 0~1

    # 주파수 근거 (특허 기반)
    freq_analysis    = Column(JSON)        # FFT 분석 근거
    fault_code       = Column(String)      # 고장 코드

    # 평가
    is_adopted       = Column(Boolean, default=False)
    rating           = Column(Integer, nullable=True)    # 1~5 사용자 평점
    rating_comment   = Column(Text, nullable=True)
    helpful_count    = Column(Integer, default=0)        # 다른 사용자 도움됨

    created_at       = Column(DateTime, default=datetime.utcnow)
    adopted_at       = Column(DateTime, nullable=True)


class Escrow(Base):
    """
    에스크로 — 보상금 안전 보관
    현상금 게시 시 예치 → 채택 시 자동 지급 → 7일 내 이의 없으면 확정
    """
    __tablename__ = "escrows"

    id               = Column(String, primary_key=True)
    bounty_id        = Column(String, ForeignKey("bounties.id"), unique=True)
    user_id          = Column(String, ForeignKey("users.id"))   # 예치자
    expert_id        = Column(String, nullable=True)             # 수취자

    # 금액 분배 (채택 시)
    total_amount_krw    = Column(Integer, default=0)   # 총 현상금 (원)
    total_amount_points = Column(Integer, default=0)   # 총 현상금 (포인트)

    # 수수료 계산
    platform_fee_rate   = Column(Float, default=0.15)  # 15%
    insurance_fee_rate  = Column(Float, default=0.05)  # 5%
    tax_rate            = Column(Float, default=0.033) # 3.3% 원천징수

    # 실제 분배 금액
    expert_net_krw      = Column(Integer, default=0)   # 전문가 실수령 (원)
    expert_net_points   = Column(Integer, default=0)   # 전문가 실수령 (포인트)
    platform_fee_krw    = Column(Integer, default=0)   # 플랫폼 수수료
    insurance_pool_krw  = Column(Integer, default=0)   # 보험 풀 적립
    tax_withheld_krw    = Column(Integer, default=0)   # 원천징수액

    # 상태
    status              = Column(SAEnum(EscrowStatus), default=EscrowStatus.HELD)
    held_at             = Column(DateTime, default=datetime.utcnow)
    release_after       = Column(DateTime, nullable=True)  # 7일 후 자동 확정
    released_at         = Column(DateTime, nullable=True)
    dispute_deadline    = Column(DateTime, nullable=True)  # 이의신청 기한


class Transaction(Base):
    """
    모든 금전 거래 기록 — 회계 투명성
    """
    __tablename__ = "transactions"

    id               = Column(String, primary_key=True)
    user_id          = Column(String, ForeignKey("users.id"), index=True)
    related_id       = Column(String, index=True)      # bounty_id, escrow_id 등

    type             = Column(SAEnum(TransactionType))
    amount_krw       = Column(Integer, default=0)      # 원화 (음수=출금)
    amount_points    = Column(Integer, default=0)      # 포인트 (음수=차감)
    balance_after_krw    = Column(Integer, default=0)  # 거래 후 잔액
    balance_after_points = Column(Integer, default=0)

    description      = Column(Text)
    receipt_number   = Column(String)                  # 영수증 번호
    tax_invoice      = Column(Boolean, default=False)  # 세금계산서 발행 여부

    created_at       = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════
# 3. 보험 시스템
# ═══════════════════════════════════════════════════════════════

class InsurancePolicy(Base):
    """
    보험 가입 정보
    - 사용자 보험: 잘못된 진단으로 인한 수리비 보상
    - 전문가 배상 보험: 잘못된 답변으로 인한 배상 책임 보호
    """
    __tablename__ = "insurance_policies"

    id               = Column(String, primary_key=True)
    user_id          = Column(String, ForeignKey("users.id"), index=True)
    policy_type      = Column(SAEnum(InsuranceType))

    # 보장 내용
    coverage_per_case = Column(Integer)    # 건당 최대 보장 (원)
    monthly_premium   = Column(Integer)    # 월 보험료 (원)
    deductible        = Column(Integer, default=0)  # 자기부담금

    # 상태
    is_active         = Column(Boolean, default=True)
    start_date        = Column(DateTime, default=datetime.utcnow)
    end_date          = Column(DateTime, nullable=True)
    auto_renew        = Column(Boolean, default=True)

    # 이용 현황
    total_claims      = Column(Integer, default=0)   # 누적 청구 건수
    total_paid        = Column(Integer, default=0)   # 누적 지급 (원)
    remaining_annual  = Column(Integer)              # 연간 한도 잔여액

    created_at        = Column(DateTime, default=datetime.utcnow)


class InsuranceClaim(Base):
    """
    보험 청구
    사용자 또는 전문가가 손해 발생 시 청구
    """
    __tablename__ = "insurance_claims"

    id                  = Column(String, primary_key=True)
    policy_id           = Column(String, ForeignKey("insurance_policies.id"))
    claimant_id         = Column(String, ForeignKey("users.id"), index=True)
    bounty_id           = Column(String, ForeignKey("bounties.id"), nullable=True)
    answer_id           = Column(String, ForeignKey("bounty_answers.id"), nullable=True)

    # 청구 내용
    claim_type          = Column(String)   # "wrong_diagnosis" / "expert_liability"
    description         = Column(Text)
    claimed_amount      = Column(Integer)  # 청구 금액 (원)
    evidence_urls       = Column(JSON, default=list)  # 증빙 자료 URL

    # 처리 결과
    approved_amount     = Column(Integer, default=0)  # 승인 금액
    rejection_reason    = Column(Text, nullable=True)
    status              = Column(String, default="pending")  # pending/approved/rejected
    processed_at        = Column(DateTime, nullable=True)
    processor_id        = Column(String, nullable=True)   # 처리자

    created_at          = Column(DateTime, default=datetime.utcnow)


class InsurancePool(Base):
    """
    보험 적립금 풀
    수수료의 5%가 적립되어 보험금 지급 재원
    """
    __tablename__ = "insurance_pool"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    date             = Column(DateTime, default=datetime.utcnow)

    total_reserve    = Column(BigInteger, default=0)   # 총 적립금 (원)
    monthly_premium_collected = Column(Integer, default=0)  # 이번 달 보험료 수입
    monthly_claims_paid       = Column(Integer, default=0)  # 이번 달 지급액
    monthly_fee_contribution  = Column(Integer, default=0)  # 수수료 적립분

    reserve_ratio    = Column(Float, default=0.0)  # 적립금 비율 (지급능력 지표)


# ═══════════════════════════════════════════════════════════════
# 4. 분쟁 & 중재 시스템
# ═══════════════════════════════════════════════════════════════

class Dispute(Base):
    """
    분쟁 처리
    채택 후 7일 이내 이의신청 가능
    마스터급 이상 전문가 3인이 중재
    """
    __tablename__ = "disputes"

    id               = Column(String, primary_key=True)
    bounty_id        = Column(String, ForeignKey("bounties.id"), index=True)
    answer_id        = Column(String, ForeignKey("bounty_answers.id"))
    complainant_id   = Column(String, ForeignKey("users.id"))   # 이의신청자
    respondent_id    = Column(String, ForeignKey("users.id"))   # 피신청자

    # 이의신청 내용
    reason           = Column(Text)
    evidence_urls    = Column(JSON, default=list)
    claimed_refund   = Column(Integer, default=0)  # 환불 요청액

    # 중재 결과
    status           = Column(SAEnum(DisputeStatus), default=DisputeStatus.PENDING)
    mediator_ids     = Column(JSON, default=list)  # 중재자 3인 ID
    verdict          = Column(String, nullable=True)  # "user_win"/"expert_win"/"split"
    verdict_reason   = Column(Text, nullable=True)
    refund_amount    = Column(Integer, default=0)
    penalty_points   = Column(Integer, default=0)  # 패소측 페널티 포인트

    created_at       = Column(DateTime, default=datetime.utcnow)
    resolved_at      = Column(DateTime, nullable=True)
    deadline         = Column(DateTime)  # 7일 처리 기한


class MediationVote(Base):
    """중재자 투표"""
    __tablename__ = "mediation_votes"

    id               = Column(String, primary_key=True)
    dispute_id       = Column(String, ForeignKey("disputes.id"), index=True)
    mediator_id      = Column(String, ForeignKey("users.id"))

    vote             = Column(String)   # "user_win"/"expert_win"/"split"
    opinion          = Column(Text)
    voted_at         = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════
# 5. 커뮤니티 & 소셜
# ═══════════════════════════════════════════════════════════════

class SoundContribution(Base):
    """
    소리 데이터 기여
    정상 소리 / 고장 소리를 제공하면 포인트 적립
    전문가 검증 시 추가 포인트
    """
    __tablename__ = "sound_contributions"

    id               = Column(String, primary_key=True)
    contributor_id   = Column(String, ForeignKey("users.id"), index=True)

    # 차량 정보
    vehicle_brand    = Column(String)
    vehicle_model    = Column(String)
    vehicle_year     = Column(Integer)
    mileage          = Column(Integer)
    engine_cc        = Column(Integer)

    # 소리 정보
    sound_type       = Column(String)   # "normal"/"fault"
    component        = Column(String)   # engine/bearing/brake...
    fault_code       = Column(String, nullable=True)
    description      = Column(Text)
    sound_session_id = Column(String)   # 실제 녹음 데이터 참조

    # 검증
    verified_by      = Column(String, nullable=True)  # 검증한 전문가 ID
    verification_status = Column(String, default="pending")  # pending/verified/rejected
    verified_at      = Column(DateTime, nullable=True)

    # 보상
    points_awarded   = Column(Integer, default=0)
    bonus_points     = Column(Integer, default=0)  # 검증 통과 보너스

    created_at       = Column(DateTime, default=datetime.utcnow)


class UserConnection(Base):
    """
    사용자 연결 — 비슷한 차량 친구 매칭
    같은 차종, 같은 증상의 사람들 연결
    """
    __tablename__ = "user_connections"

    id               = Column(String, primary_key=True)
    user_a_id        = Column(String, ForeignKey("users.id"), index=True)
    user_b_id        = Column(String, ForeignKey("users.id"), index=True)

    connection_type  = Column(String)   # "same_vehicle"/"same_symptom"/"referral"
    vehicle_match    = Column(String)   # 공통 차량 정보
    match_score      = Column(Float)    # 매칭 점수

    is_mutual        = Column(Boolean, default=False)  # 상호 동의
    created_at       = Column(DateTime, default=datetime.utcnow)
