"""
Dr. Vroom — 포인트 경제 서비스 (Points Economy Service)
════════════════════════════════════════════════════════

포인트 설계:
  1 포인트 = 플랫폼 내부 가상 재화 (통화 아님)
  현금 충전 시 → 결제 대행사(PG) 통해 포인트 발급 (IAP)
  포인트 사용  → 진단/현상금/보험/구독 결제
  포인트 획득  → 기여/채택보상/출석/이벤트

포인트 단가 (참고, 앱 내 고정):
  100 포인트 = 진단 1회
  월 구독 (Pro) = 300 포인트
  현상금 최소  = 100 포인트

법적 지위 (글로벌 공통):
  - 앱 내 가상 재화 (Virtual Goods)
  - 전자상거래 포인트/마일리지와 동일
  - 현금 인출 없음 → 자금세탁방지법 비해당
  - 환율 위험 없음 → 단일 통화 내부 운영
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime, timedelta
import uuid

from app.db.points_schema import (
    PointWallet, PointTransaction, PointTxType,
    UserGradeV2, UserTier, ExpertProfileV2
)


# ══════════════════════════════════════════════════════════════
# 포인트 단가 상수
# ══════════════════════════════════════════════════════════════

# 진단 비용
DIAGNOSIS_COST_FREE    = 0    # Free 등급: 월 3회 무료
DIAGNOSIS_COST_PAY     = 100  # 추가 진단 1회당 포인트

# 등급 구독 비용 (월/포인트)
SUBSCRIPTION_COST = {
    UserTier.FREE:       0,
    UserTier.STARTER:  300,   # 월 300P
    UserTier.PRO:      900,   # 월 900P
    UserTier.EXPERT:  2700,   # 월 2700P
}

# 등급별 월 무료 진단 횟수
FREE_DIAGNOSES_PER_MONTH = {
    UserTier.FREE:     3,
    UserTier.STARTER:  30,
    UserTier.PRO:      -1,    # 무제한
    UserTier.EXPERT:   -1,    # 무제한
}

# 현상금 최소/최대 포인트
BOUNTY_MIN_POINTS = 100
BOUNTY_MAX_POINTS = {
    UserTier.FREE:     500,
    UserTier.STARTER: 2000,
    UserTier.PRO:     10000,
    UserTier.EXPERT:  50000,
}

# 수수료 (포인트 내부 공제)
PLATFORM_FEE_RATE  = 0.15   # 15% → 운영비
INSURANCE_FEE_RATE = 0.05   # 5%  → 보험 적립금
EXPERT_NET_RATE    = 0.80   # 80% → 전문가 지급

# 기여 보상
CONTRIBUTION_POINTS  = 10   # 소리 제출
VERIFICATION_BONUS   = 40   # 전문가 검증 통과
DAILY_LOGIN_POINTS   = 5    # 출석 체크
REFERRAL_POINTS      = 50   # 친구 초대

# 보험 (포인트 납부/보상)
INSURANCE_PLANS = {
    "none":     {"monthly_pts": 0,   "coverage_pts": 0,      "annual_pts": 0},
    "basic":    {"monthly_pts": 0,   "coverage_pts": 500,    "annual_pts": 1000},    # 무료
    "standard": {"monthly_pts": 50,  "coverage_pts": 2000,   "annual_pts": 6000},    # 월 50P
    "premium":  {"monthly_pts": 100, "coverage_pts": 5000,   "annual_pts": 20000},   # 월 100P
}


# ══════════════════════════════════════════════════════════════
# WALLET SERVICE
# ══════════════════════════════════════════════════════════════

class PointsService:
    """포인트 지갑 서비스 — 모든 포인트 거래 처리"""

    @staticmethod
    async def get_or_create_wallet(user_id: str, db: AsyncSession) -> PointWallet:
        """지갑 조회 또는 생성"""
        result = await db.execute(
            select(PointWallet).where(PointWallet.user_id == user_id)
        )
        wallet = result.scalar_one_or_none()
        if not wallet:
            wallet = PointWallet(user_id=user_id, balance=50)  # 신규 가입 50P 지급
            db.add(wallet)
            # Welcome bonus transaction
            await PointsService._record_tx(
                db=db,
                user_id=user_id,
                tx_type=PointTxType.DAILY_BONUS,
                amount=50,
                balance_after=50,
                ref_type="system",
                description="🎉 닥터브릉이 가입 환영 포인트 50P",
            )
            await db.flush()
        return wallet

    @staticmethod
    async def get_balance(user_id: str, db: AsyncSession) -> dict:
        """잔액 조회"""
        wallet = await PointsService.get_or_create_wallet(user_id, db)
        return {
            "balance": wallet.balance,
            "locked": wallet.locked,
            "available": wallet.balance - wallet.locked,
            "lifetime_earned": wallet.lifetime_earned,
            "lifetime_spent": wallet.lifetime_spent,
        }

    @staticmethod
    async def earn(
        db: AsyncSession,
        user_id: str,
        amount: int,
        tx_type: PointTxType,
        ref_id: str = None,
        ref_type: str = None,
        description: str = "",
    ) -> dict:
        """포인트 적립"""
        if amount <= 0:
            raise ValueError("적립 포인트는 0보다 커야 합니다.")

        wallet = await PointsService.get_or_create_wallet(user_id, db)
        wallet.balance += amount
        wallet.lifetime_earned += amount
        wallet.monthly_earned += amount
        wallet.updated_at = datetime.utcnow()

        await PointsService._record_tx(
            db=db,
            user_id=user_id,
            tx_type=tx_type,
            amount=amount,
            balance_after=wallet.balance,
            locked_after=wallet.locked,
            ref_id=ref_id,
            ref_type=ref_type,
            description=description,
        )
        return {"balance": wallet.balance, "earned": amount}

    @staticmethod
    async def spend(
        db: AsyncSession,
        user_id: str,
        amount: int,
        tx_type: PointTxType,
        ref_id: str = None,
        ref_type: str = None,
        description: str = "",
    ) -> dict:
        """포인트 차감"""
        if amount <= 0:
            raise ValueError("차감 포인트는 0보다 커야 합니다.")

        wallet = await PointsService.get_or_create_wallet(user_id, db)
        available = wallet.balance - wallet.locked
        if available < amount:
            raise ValueError(f"포인트 부족. 필요: {amount}P, 가용: {available}P")

        wallet.balance -= amount
        wallet.lifetime_spent += amount
        wallet.monthly_spent += amount
        wallet.updated_at = datetime.utcnow()

        await PointsService._record_tx(
            db=db,
            user_id=user_id,
            tx_type=tx_type,
            amount=-amount,
            balance_after=wallet.balance,
            locked_after=wallet.locked,
            ref_id=ref_id,
            ref_type=ref_type,
            description=description,
        )
        return {"balance": wallet.balance, "spent": amount}

    @staticmethod
    async def lock(
        db: AsyncSession,
        user_id: str,
        amount: int,
        ref_id: str,
        description: str = "",
    ) -> dict:
        """포인트 에스크로 잠금 (현상금 예치)"""
        wallet = await PointsService.get_or_create_wallet(user_id, db)
        available = wallet.balance - wallet.locked
        if available < amount:
            raise ValueError(f"잠금 포인트 부족. 필요: {amount}P, 가용: {available}P")

        wallet.locked += amount
        wallet.updated_at = datetime.utcnow()

        await PointsService._record_tx(
            db=db,
            user_id=user_id,
            tx_type=PointTxType.BOUNTY_POST,
            amount=-amount,
            balance_after=wallet.balance,
            locked_after=wallet.locked,
            ref_id=ref_id,
            ref_type="escrow_lock",
            description=description or f"현상금 에스크로 잠금: {amount}P",
        )
        return {"balance": wallet.balance, "locked": wallet.locked}

    @staticmethod
    async def release_escrow(
        db: AsyncSession,
        escrow_id: str,
        poster_id: str,
        expert_id: str,
        total_points: int,
    ) -> dict:
        """에스크로 해제 → 수수료 공제 후 전문가 지급"""
        platform_fee = int(total_points * PLATFORM_FEE_RATE)
        insurance_fee = int(total_points * INSURANCE_FEE_RATE)
        expert_net = total_points - platform_fee - insurance_fee

        # 포스터 지갑: 잠금 해제 (잔액은 이미 차감됨)
        poster_wallet = await PointsService.get_or_create_wallet(poster_id, db)
        poster_wallet.locked = max(0, poster_wallet.locked - total_points)
        poster_wallet.lifetime_spent += total_points
        poster_wallet.updated_at = datetime.utcnow()

        # 전문가 지급
        await PointsService.earn(
            db=db,
            user_id=expert_id,
            amount=expert_net,
            tx_type=PointTxType.BOUNTY_REWARD,
            ref_id=escrow_id,
            ref_type="escrow_release",
            description=f"현상금 채택 보상 {expert_net}P (수수료 공제 후)",
        )

        # 전문가 프로필 업데이트
        expert_res = await db.execute(
            select(ExpertProfileV2).where(ExpertProfileV2.user_id == expert_id)
        )
        expert = expert_res.scalar_one_or_none()
        if expert:
            expert.total_earned_points += expert_net
            expert.updated_at = datetime.utcnow()

        return {
            "total_points": total_points,
            "platform_fee": platform_fee,
            "insurance_fee": insurance_fee,
            "expert_net": expert_net,
            "breakdown": {
                "expert_pct": "80%",
                "platform_pct": "15%",
                "insurance_pct": "5%",
                "tax": "없음 (가상재화)",
            }
        }

    @staticmethod
    async def refund_escrow(
        db: AsyncSession,
        escrow_id: str,
        poster_id: str,
        total_points: int,
        reason: str = "만료 환불",
    ) -> dict:
        """에스크로 환불 (만료/분쟁 패소)"""
        poster_wallet = await PointsService.get_or_create_wallet(poster_id, db)
        poster_wallet.locked = max(0, poster_wallet.locked - total_points)
        poster_wallet.updated_at = datetime.utcnow()

        await PointsService._record_tx(
            db=db,
            user_id=poster_id,
            tx_type=PointTxType.REFUND,
            amount=0,   # 잔액 변화 없음 (잠금만 해제)
            balance_after=poster_wallet.balance,
            locked_after=poster_wallet.locked,
            ref_id=escrow_id,
            ref_type="escrow_refund",
            description=f"에스크로 환불: {reason} ({total_points}P 잠금 해제)",
        )
        return {"refunded_points": total_points, "reason": reason}

    @staticmethod
    async def _record_tx(
        db: AsyncSession,
        user_id: str,
        tx_type: PointTxType,
        amount: int,
        balance_after: int,
        locked_after: int = 0,
        ref_id: str = None,
        ref_type: str = None,
        description: str = "",
    ):
        """거래 내역 기록"""
        tx = PointTransaction(
            id=str(uuid.uuid4()),
            user_id=user_id,
            tx_type=tx_type,
            amount=amount,
            balance_after=balance_after,
            locked_after=locked_after,
            ref_id=ref_id,
            ref_type=ref_type,
            description=description,
            note="앱 내 가상 재화 거래 — 통화 아님",
        )
        db.add(tx)

    @staticmethod
    async def check_diagnosis_available(
        user_id: str,
        db: AsyncSession,
    ) -> dict:
        """진단 가능 여부 확인 (등급 + 포인트)"""
        grade_res = await db.execute(
            select(UserGradeV2).where(UserGradeV2.user_id == user_id)
        )
        grade = grade_res.scalar_one_or_none()
        if not grade:
            grade = UserGradeV2(user_id=user_id)
            db.add(grade)
            await db.flush()

        tier = grade.tier or UserTier.FREE
        monthly_limit = FREE_DIAGNOSES_PER_MONTH[tier]

        # 월 초기화 체크
        now = datetime.utcnow()
        if grade.month_reset_at.month != now.month or grade.month_reset_at.year != now.year:
            grade.monthly_diagnoses = 0
            grade.month_reset_at = now

        # 무제한 등급
        if monthly_limit == -1:
            return {"available": True, "cost_points": 0, "reason": "무제한 등급"}

        # 무료 횟수 남음
        if grade.monthly_diagnoses < monthly_limit:
            remaining = monthly_limit - grade.monthly_diagnoses
            return {
                "available": True,
                "cost_points": 0,
                "reason": f"월 무료 진단 잔여: {remaining}회",
                "monthly_used": grade.monthly_diagnoses,
                "monthly_limit": monthly_limit,
            }

        # 포인트로 추가 진단
        wallet = await PointsService.get_or_create_wallet(user_id, db)
        available_pts = wallet.balance - wallet.locked
        if available_pts >= DIAGNOSIS_COST_PAY:
            return {
                "available": True,
                "cost_points": DIAGNOSIS_COST_PAY,
                "reason": f"포인트 차감 진단: {DIAGNOSIS_COST_PAY}P",
                "balance": available_pts,
            }

        return {
            "available": False,
            "cost_points": DIAGNOSIS_COST_PAY,
            "reason": "월 무료 진단 소진, 포인트 부족",
            "balance": available_pts,
            "needed": DIAGNOSIS_COST_PAY,
        }

    @staticmethod
    async def use_diagnosis(user_id: str, db: AsyncSession) -> dict:
        """진단 1회 사용 처리"""
        check = await PointsService.check_diagnosis_available(user_id, db)
        if not check["available"]:
            raise ValueError(check["reason"])

        grade_res = await db.execute(
            select(UserGradeV2).where(UserGradeV2.user_id == user_id)
        )
        grade = grade_res.scalar_one_or_none()
        if grade:
            grade.monthly_diagnoses += 1
            grade.total_diagnoses += 1

        # 포인트 차감이 필요한 경우
        if check["cost_points"] > 0:
            await PointsService.spend(
                db=db,
                user_id=user_id,
                amount=check["cost_points"],
                tx_type=PointTxType.DIAGNOSIS_USE,
                ref_type="diagnosis",
                description=f"진단 1회 사용: {check['cost_points']}P",
            )

        return {"success": True, "cost": check["cost_points"]}
