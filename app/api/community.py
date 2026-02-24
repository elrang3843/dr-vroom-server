"""
👥 Community API — 친구 시스템 & 감사 표현
- 친구 연결, 같은 차 모임, 감사 포인트 선물

Endpoints:
  POST /api/v1/community/friends/request        — 친구 요청
  POST /api/v1/community/friends/{id}/accept    — 친구 수락
  GET  /api/v1/community/friends                — 친구 목록
  POST /api/v1/community/gratitude/send         — 감사 포인트 보내기
  POST /api/v1/community/thankyou               — 감사 노트 작성
  GET  /api/v1/community/thankyou/public        — 공개 감사 노트
  POST /api/v1/community/invite/generate        — 초대 코드 생성
  POST /api/v1/community/invite/use             — 초대 코드 사용
  GET  /api/v1/community/groups/my-vehicle      — 내 차 모임 찾기
  GET  /api/v1/community/notifications          — 알림 목록
  POST /api/v1/community/notifications/read-all — 전체 읽음 처리
  GET  /api/v1/community/feed                   — 커뮤니티 피드
  POST /api/v1/community/feed/post              — 게시물 작성
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, and_
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime
import uuid
import secrets
import string

from app.db.database import get_db, User
from app.db.community_schema import (
    Friend, FriendStatus, FriendHelp, InviteCode, InviteUsage,
    Gratitude, GratitudeType, ThankYouNote,
    CommunityPost, PostComment, PostLike,
    VehicleGroup, VehicleGroupMember,
    Notification, ConnectionReason
)
from app.db.points_schema import PointWallet, PointTransaction, PointTxType
from app.api.auth import get_current_user

router = APIRouter(prefix="/api/v1/community", tags=["👥 Community"])


# ─── Pydantic Models ──────────────────────────────────────────

class FriendRequestModel(BaseModel):
    receiver_id: str
    message: Optional[str] = Field(None, max_length=300)
    connection_reason: Optional[ConnectionReason] = None
    context_data: Optional[Dict] = None


class GratitudeSendModel(BaseModel):
    receiver_id: str
    gratitude_type: GratitudeType = GratitudeType.THANKS_POINTS
    points_amount: int = Field(0, ge=0, le=1000)
    message: Optional[str] = Field(None, max_length=500)
    ref_id: Optional[str] = None
    ref_type: Optional[str] = None


class ThankYouNoteModel(BaseModel):
    receiver_id: str
    message: str = Field(..., max_length=1000)
    is_public: bool = True
    is_anonymous: bool = False
    ref_id: Optional[str] = None
    ref_type: Optional[str] = None


class CommunityPostModel(BaseModel):
    post_type: str = Field(..., description="sound_share/repair_story/tip/question/warning")
    title: str = Field(..., max_length=200)
    content: str
    vehicle_info: Optional[Dict] = None
    component: Optional[str] = None
    audio_url: Optional[str] = None
    memory_id: Optional[str] = None
    report_id: Optional[str] = None
    is_public: bool = True
    tags: List[str] = []


class VehicleGroupJoinModel(BaseModel):
    vehicle_type: str
    vehicle_brand: str
    vehicle_model: str
    vehicle_year: Optional[int] = None
    vehicle_info: Optional[Dict] = None


# ─── Helpers ─────────────────────────────────────────────────

async def award_points(db, user_id, amount, tx_type, description, ref_id, ref_type="community"):
    wallet_result = await db.execute(
        select(PointWallet).where(PointWallet.user_id == user_id)
    )
    wallet = wallet_result.scalar_one_or_none()
    if not wallet:
        wallet = PointWallet(user_id=user_id, balance=0, locked=0)
        db.add(wallet)
        await db.flush()

    wallet.balance += amount
    wallet.lifetime_earned += amount
    wallet.monthly_earned += amount
    wallet.updated_at = datetime.utcnow()

    tx = PointTransaction(
        id=str(uuid.uuid4()),
        user_id=user_id,
        tx_type=tx_type,
        amount=amount,
        balance_after=wallet.balance,
        ref_id=ref_id,
        ref_type=ref_type,
        description=description,
        note="앱 내 가상 재화 거래",
    )
    db.add(tx)


async def deduct_points(db, user_id, amount, tx_type, description, ref_id, ref_type="community"):
    wallet_result = await db.execute(
        select(PointWallet).where(PointWallet.user_id == user_id)
    )
    wallet = wallet_result.scalar_one_or_none()
    if not wallet or wallet.balance < amount:
        raise HTTPException(status_code=400, detail=f"포인트가 부족합니다. (필요: {amount}P)")

    wallet.balance -= amount
    wallet.lifetime_spent += amount
    wallet.monthly_spent += amount
    wallet.updated_at = datetime.utcnow()

    tx = PointTransaction(
        id=str(uuid.uuid4()),
        user_id=user_id,
        tx_type=tx_type,
        amount=-amount,
        balance_after=wallet.balance,
        ref_id=ref_id,
        ref_type=ref_type,
        description=description,
        note="앱 내 가상 재화 거래",
    )
    db.add(tx)


async def push_notification(db, user_id, notif_type, title, body, ref_id=None, ref_type=None):
    notif = Notification(
        id=str(uuid.uuid4()),
        user_id=user_id,
        notif_type=notif_type,
        title=title,
        body=body,
        ref_id=ref_id,
        ref_type=ref_type,
    )
    db.add(notif)


# ═══════════════════════════════════════════════════
# 1. FRIEND SYSTEM
# ═══════════════════════════════════════════════════

@router.post("/friends/request", summary="친구 요청 보내기")
async def send_friend_request(
    req: FriendRequestModel,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """👥 친구 요청을 보냅니다."""
    if req.receiver_id == current_user.id:
        raise HTTPException(status_code=400, detail="자기 자신에게 친구 요청을 보낼 수 없습니다.")

    # 이미 친구인지 확인
    existing = await db.execute(
        select(Friend).where(
            or_(
                and_(Friend.requester_id == current_user.id, Friend.receiver_id == req.receiver_id),
                and_(Friend.requester_id == req.receiver_id, Friend.receiver_id == current_user.id),
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="이미 친구이거나 요청 중입니다.")

    # 받는 사람 존재 확인
    receiver = await db.execute(select(User).where(User.id == req.receiver_id))
    receiver_user = receiver.scalar_one_or_none()
    if not receiver_user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    friend_id = str(uuid.uuid4())
    friend = Friend(
        id=friend_id,
        requester_id=current_user.id,
        receiver_id=req.receiver_id,
        status=FriendStatus.PENDING,
        connection_reason=req.connection_reason,
        context_data=req.context_data or {},
        message=req.message,
    )
    db.add(friend)

    # 알림
    await push_notification(
        db, req.receiver_id,
        "friend_request",
        "👥 새 친구 요청이 왔어요!",
        f"{current_user.username}님이 친구를 요청했습니다." + (f" '{req.message}'" if req.message else ""),
        ref_id=friend_id,
        ref_type="friend",
    )

    await db.commit()

    return {
        "success": True,
        "friend_id": friend_id,
        "message": f"✅ {receiver_user.username}님에게 친구 요청을 보냈습니다."
    }


@router.post("/friends/{friend_id}/accept", summary="친구 요청 수락")
async def accept_friend_request(
    friend_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """✅ 친구 요청을 수락합니다."""
    result = await db.execute(
        select(Friend).where(
            Friend.id == friend_id,
            Friend.receiver_id == current_user.id,
            Friend.status == FriendStatus.PENDING,
        )
    )
    friend = result.scalar_one_or_none()
    if not friend:
        raise HTTPException(status_code=404, detail="친구 요청을 찾을 수 없습니다.")

    friend.status = FriendStatus.ACCEPTED
    friend.accepted_at = datetime.utcnow()

    # 양방향 알림
    await push_notification(
        db, friend.requester_id,
        "friend_accepted",
        "🎉 친구 요청이 수락되었어요!",
        f"{current_user.username}님이 친구 요청을 수락했습니다!",
        ref_id=friend_id,
        ref_type="friend",
    )

    # 양쪽 모두 보너스 포인트 (첫 연결 선물)
    await award_points(db, friend.requester_id, 20, PointTxType.DAILY_BONUS,
                       "친구 연결 보너스", friend_id, "friend")
    await award_points(db, current_user.id, 20, PointTxType.DAILY_BONUS,
                       "친구 연결 보너스", friend_id, "friend")

    await db.commit()
    return {"success": True, "message": "🎉 친구가 되었습니다! 함께 차량 정보를 공유해보세요."}


@router.get("/friends", summary="친구 목록")
async def get_friends(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """👥 내 친구 목록"""
    result = await db.execute(
        select(Friend).where(
            or_(
                Friend.requester_id == current_user.id,
                Friend.receiver_id == current_user.id,
            ),
            Friend.status == FriendStatus.ACCEPTED,
        )
    )
    friends = result.scalars().all()

    friend_list = []
    for f in friends:
        other_id = f.receiver_id if f.requester_id == current_user.id else f.requester_id
        user_result = await db.execute(select(User).where(User.id == other_id))
        other_user = user_result.scalar_one_or_none()
        if other_user:
            friend_list.append({
                "friend_id": f.id,
                "user_id": other_user.id,
                "username": other_user.username,
                "role": other_user.role,
                "connection_reason": f.connection_reason,
                "shared_diagnoses": f.shared_diagnoses,
                "helped_count": f.helped_count,
                "connected_at": f.accepted_at.isoformat() if f.accepted_at else None,
            })

    # 대기 중인 요청
    pending_result = await db.execute(
        select(Friend).where(
            Friend.receiver_id == current_user.id,
            Friend.status == FriendStatus.PENDING,
        )
    )
    pending = pending_result.scalars().all()

    return {
        "friends": friend_list,
        "total_friends": len(friend_list),
        "pending_requests": len(pending),
        "pending_list": [
            {
                "friend_id": p.id,
                "requester_id": p.requester_id,
                "message": p.message,
                "connection_reason": p.connection_reason,
                "created_at": p.created_at.isoformat(),
            }
            for p in pending
        ]
    }


# ═══════════════════════════════════════════════════
# 2. GRATITUDE SYSTEM
# ═══════════════════════════════════════════════════

@router.post("/gratitude/send", summary="감사 포인트 보내기")
async def send_gratitude(
    req: GratitudeSendModel,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    🙏 도움을 준 친구에게 감사를 표현합니다.

    포인트 선물 / 별점 / 도움됨 투표 등 다양한 방법으로 감사 표현
    - 포인트 선물: 내 포인트에서 차감 → 상대방 포인트에 추가
    - 법적으로 '앱 내 가상 재화 증여' (현금화 불가)
    """
    if req.receiver_id == current_user.id:
        raise HTTPException(status_code=400, detail="자기 자신에게 감사를 보낼 수 없습니다.")

    # 받는 사람 확인
    receiver = (await db.execute(select(User).where(User.id == req.receiver_id))).scalar_one_or_none()
    if not receiver:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    gratitude_id = str(uuid.uuid4())

    if req.gratitude_type == GratitudeType.THANKS_POINTS and req.points_amount > 0:
        # 포인트 차감 (보내는 사람)
        await deduct_points(
            db, current_user.id, req.points_amount,
            PointTxType.PLATFORM_FEE,  # 실제로는 THANKS_SEND로 만들 수 있지만 기존 enum 사용
            f"감사 포인트 선물: {receiver.username}에게",
            gratitude_id, "gratitude"
        )
        # 포인트 지급 (받는 사람)
        await award_points(
            db, req.receiver_id, req.points_amount,
            PointTxType.REFERRAL,  # 감사 수신으로 사용
            f"감사 포인트 수신: {current_user.username}으로부터",
            gratitude_id, "gratitude"
        )

        # 알림
        await push_notification(
            db, req.receiver_id,
            "gratitude_received",
            f"🙏 {current_user.username}님이 감사 포인트를 보냈어요!",
            f"{req.points_amount}P 수신!" + (f" '{req.message}'" if req.message else ""),
            ref_id=gratitude_id,
            ref_type="gratitude",
        )

    gratitude = Gratitude(
        id=gratitude_id,
        sender_id=current_user.id,
        receiver_id=req.receiver_id,
        gratitude_type=req.gratitude_type,
        points_amount=req.points_amount,
        message=req.message,
        ref_id=req.ref_id,
        ref_type=req.ref_type,
        is_processed=True,
    )
    db.add(gratitude)

    await db.commit()

    return {
        "success": True,
        "gratitude_id": gratitude_id,
        "receiver": receiver.username,
        "points_sent": req.points_amount,
        "message": f"🙏 {receiver.username}님에게 감사를 전했습니다!"
    }


@router.post("/thankyou", summary="감사 노트 작성")
async def write_thankyou_note(
    req: ThankYouNoteModel,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """💌 포인트 없이도 감사 메시지를 남길 수 있어요."""
    receiver = (await db.execute(select(User).where(User.id == req.receiver_id))).scalar_one_or_none()
    if not receiver:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    note_id = str(uuid.uuid4())
    note = ThankYouNote(
        id=note_id,
        sender_id=current_user.id,
        receiver_id=req.receiver_id,
        message=req.message,
        is_public=req.is_public,
        is_anonymous=req.is_anonymous,
        ref_id=req.ref_id,
        ref_type=req.ref_type,
    )
    db.add(note)

    sender_name = "익명" if req.is_anonymous else current_user.username
    await push_notification(
        db, req.receiver_id,
        "gratitude_received",
        f"💌 {sender_name}님이 감사 메시지를 보냈어요!",
        req.message[:100] + "..." if len(req.message) > 100 else req.message,
        ref_id=note_id, ref_type="thankyou_note",
    )

    await db.commit()
    return {"success": True, "note_id": note_id, "message": "💌 감사 노트가 전달되었습니다."}


@router.get("/thankyou/public", summary="공개 감사 노트 피드")
async def get_public_thankyou_notes(
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """💌 커뮤니티의 공개 감사 노트들을 보여줍니다."""
    result = await db.execute(
        select(ThankYouNote)
        .where(ThankYouNote.is_public == True)
        .order_by(ThankYouNote.created_at.desc())
        .limit(limit)
    )
    notes = result.scalars().all()

    return {
        "notes": [
            {
                "id": n.id,
                "sender": "익명" if n.is_anonymous else n.sender_id,
                "receiver_id": n.receiver_id,
                "message": n.message,
                "likes_count": n.likes_count,
                "ref_type": n.ref_type,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notes
        ]
    }


# ═══════════════════════════════════════════════════
# 3. INVITE SYSTEM
# ═══════════════════════════════════════════════════

@router.post("/invite/generate", summary="초대 코드 생성")
async def generate_invite_code(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    📨 나만의 초대 코드를 생성합니다.
    초대받은 친구가 첫 진단 완료 시 양쪽 모두 보너스 포인트!
    """
    # 기존 활성 코드 확인
    existing = await db.execute(
        select(InviteCode).where(
            InviteCode.owner_id == current_user.id,
            InviteCode.is_active == True,
        )
    )
    existing_code = existing.scalar_one_or_none()
    if existing_code:
        return {
            "invite_code": existing_code.id,
            "owner_bonus": existing_code.owner_bonus,
            "invitee_bonus": existing_code.invitee_bonus,
            "use_count": existing_code.use_count,
            "message": "기존 초대 코드를 사용하세요!",
        }

    # 새 코드 생성 (6자리 영숫자)
    code = "DR" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))

    invite = InviteCode(
        id=code,
        owner_id=current_user.id,
        owner_bonus=100,
        invitee_bonus=50,
    )
    db.add(invite)
    await db.commit()

    return {
        "invite_code": code,
        "owner_bonus": 100,
        "invitee_bonus": 50,
        "use_count": 0,
        "message": f"📨 초대 코드 {code}를 친구에게 공유하세요! 첫 진단 완료 시 양쪽 모두 포인트 지급.",
    }


@router.post("/invite/use", summary="초대 코드 사용")
async def use_invite_code(
    code: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """📨 초대 코드를 입력해 친구와 연결됩니다."""
    invite = (await db.execute(
        select(InviteCode).where(InviteCode.id == code, InviteCode.is_active == True)
    )).scalar_one_or_none()

    if not invite:
        raise HTTPException(status_code=404, detail="유효하지 않은 초대 코드입니다.")
    if invite.owner_id == current_user.id:
        raise HTTPException(status_code=400, detail="자신의 초대 코드는 사용할 수 없습니다.")
    if invite.use_count >= invite.max_uses:
        raise HTTPException(status_code=400, detail="초대 코드 사용 횟수가 초과되었습니다.")

    # 이미 사용했는지 확인
    existing_usage = (await db.execute(
        select(InviteUsage).where(
            InviteUsage.code_id == code,
            InviteUsage.invitee_id == current_user.id,
        )
    )).scalar_one_or_none()
    if existing_usage:
        raise HTTPException(status_code=409, detail="이미 사용한 초대 코드입니다.")

    usage = InviteUsage(
        id=str(uuid.uuid4()),
        code_id=code,
        invitee_id=current_user.id,
    )
    db.add(usage)
    invite.use_count += 1

    # 즉시 초대받은 사람에게 환영 포인트 지급
    await award_points(
        db, current_user.id, invite.invitee_bonus,
        PointTxType.REFERRAL,
        f"초대 코드 사용 보너스",
        code, "invite"
    )

    # 초대한 사람에게도 보너스
    await award_points(
        db, invite.owner_id, invite.owner_bonus,
        PointTxType.REFERRAL,
        f"친구 초대 성공 보너스",
        code, "invite"
    )

    # 자동 친구 연결
    friend_id = str(uuid.uuid4())
    friend = Friend(
        id=friend_id,
        requester_id=invite.owner_id,
        receiver_id=current_user.id,
        status=FriendStatus.ACCEPTED,
        connection_reason=ConnectionReason.REFERRAL,
        accepted_at=datetime.utcnow(),
    )
    db.add(friend)

    # 알림
    owner_user = (await db.execute(select(User).where(User.id == invite.owner_id))).scalar_one_or_none()
    await push_notification(
        db, invite.owner_id,
        "friend_accepted",
        "🎉 초대한 친구가 가입했어요!",
        f"{current_user.username}님이 초대 코드로 가입했습니다. {invite.owner_bonus}P 지급!",
    )

    await db.commit()

    return {
        "success": True,
        "invited_by": owner_user.username if owner_user else "unknown",
        "points_received": invite.invitee_bonus,
        "message": f"🎉 환영합니다! {invite.invitee_bonus}P가 지급되었고 친구로 연결되었습니다.",
    }


# ═══════════════════════════════════════════════════
# 4. VEHICLE GROUPS
# ═══════════════════════════════════════════════════

@router.get("/groups/my-vehicle", summary="내 차 모임 찾기")
async def find_vehicle_group(
    vehicle_brand: str,
    vehicle_model: str,
    vehicle_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """🚗 같은 차종의 모임을 찾습니다."""
    # 기존 모임 찾기
    result = await db.execute(
        select(VehicleGroup).where(
            VehicleGroup.vehicle_brand == vehicle_brand,
            VehicleGroup.vehicle_model == vehicle_model,
            VehicleGroup.is_active == True,
        )
    )
    groups = result.scalars().all()

    if not groups:
        # 모임이 없으면 자동 생성
        group_id = str(uuid.uuid4())
        group = VehicleGroup(
            id=group_id,
            vehicle_type=vehicle_type or "unknown",
            vehicle_brand=vehicle_brand,
            vehicle_model=vehicle_model,
            group_name=f"{vehicle_brand.title()} {vehicle_model.title()} 모임",
            description=f"{vehicle_brand.title()} {vehicle_model.title()} 오너들의 모임입니다.",
            member_count=1,
        )
        db.add(group)

        member = VehicleGroupMember(
            id=str(uuid.uuid4()),
            group_id=group_id,
            user_id=current_user.id,
        )
        db.add(member)
        await db.commit()

        return {
            "groups": [{"id": group_id, "group_name": group.group_name, "member_count": 1}],
            "message": "🚗 새 모임이 만들어졌습니다! 같은 차 오너를 초대해보세요.",
            "is_new": True,
        }

    return {
        "groups": [
            {
                "id": g.id,
                "group_name": g.group_name,
                "description": g.description,
                "member_count": g.member_count,
                "post_count": g.post_count,
                "common_issues": g.common_issues or [],
            }
            for g in groups
        ],
        "message": f"🚗 '{vehicle_brand} {vehicle_model}' 모임을 찾았습니다!",
        "is_new": False,
    }


@router.post("/groups/{group_id}/join", summary="차량 모임 가입")
async def join_vehicle_group(
    group_id: str,
    req: VehicleGroupJoinModel,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """🚗 차량 모임에 가입합니다."""
    group = (await db.execute(select(VehicleGroup).where(VehicleGroup.id == group_id))).scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="모임을 찾을 수 없습니다.")

    # 이미 가입했는지 확인
    existing = (await db.execute(
        select(VehicleGroupMember).where(
            VehicleGroupMember.group_id == group_id,
            VehicleGroupMember.user_id == current_user.id,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="이미 가입한 모임입니다.")

    member = VehicleGroupMember(
        id=str(uuid.uuid4()),
        group_id=group_id,
        user_id=current_user.id,
        vehicle_info=req.vehicle_info or {
            "type": req.vehicle_type,
            "brand": req.vehicle_brand,
            "model": req.vehicle_model,
            "year": req.vehicle_year,
        },
    )
    db.add(member)
    group.member_count += 1

    await db.commit()
    return {"success": True, "message": f"🚗 '{group.group_name}'에 가입했습니다!"}


# ═══════════════════════════════════════════════════
# 5. NOTIFICATIONS
# ═══════════════════════════════════════════════════

@router.get("/notifications", summary="알림 목록")
async def get_notifications(
    unread_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """🔔 내 알림 목록"""
    query = select(Notification).where(Notification.user_id == current_user.id)
    if unread_only:
        query = query.where(Notification.is_read == False)
    query = query.order_by(Notification.created_at.desc()).limit(limit)

    result = await db.execute(query)
    notifs = result.scalars().all()

    # 읽지 않은 알림 수
    unread_count_result = await db.execute(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == current_user.id,
            Notification.is_read == False,
        )
    )
    unread_count = unread_count_result.scalar()

    return {
        "unread_count": unread_count,
        "notifications": [
            {
                "id": n.id,
                "type": n.notif_type,
                "title": n.title,
                "body": n.body,
                "data": n.data or {},
                "ref_id": n.ref_id,
                "ref_type": n.ref_type,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifs
        ]
    }


@router.post("/notifications/read-all", summary="알림 전체 읽음")
async def read_all_notifications(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """🔔 모든 알림을 읽음으로 표시"""
    result = await db.execute(
        select(Notification).where(
            Notification.user_id == current_user.id,
            Notification.is_read == False,
        )
    )
    notifs = result.scalars().all()
    for n in notifs:
        n.is_read = True
        n.read_at = datetime.utcnow()

    await db.commit()
    return {"success": True, "marked_read": len(notifs)}


# ═══════════════════════════════════════════════════
# 6. COMMUNITY FEED
# ═══════════════════════════════════════════════════

@router.get("/feed", summary="커뮤니티 피드")
async def get_community_feed(
    post_type: Optional[str] = None,
    tag: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """📢 커뮤니티 최신 게시물"""
    query = select(CommunityPost).where(CommunityPost.is_public == True)
    if post_type:
        query = query.where(CommunityPost.post_type == post_type)
    if tag:
        query = query.where(CommunityPost.tags.contains([tag]))

    query = query.order_by(CommunityPost.created_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    posts = result.scalars().all()

    return {
        "posts": [
            {
                "id": p.id,
                "author_id": p.author_id,
                "post_type": p.post_type,
                "title": p.title,
                "content": p.content[:200] + "..." if len(p.content) > 200 else p.content,
                "component": p.component,
                "has_audio": bool(p.audio_url),
                "like_count": p.like_count,
                "comment_count": p.comment_count,
                "helpful_count": p.helpful_count,
                "tags": p.tags or [],
                "is_featured": p.is_featured,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in posts
        ]
    }


@router.post("/feed/post", summary="커뮤니티 게시물 작성")
async def create_post(
    req: CommunityPostModel,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """📢 커뮤니티에 게시물을 작성합니다."""
    post_id = str(uuid.uuid4())
    post = CommunityPost(
        id=post_id,
        author_id=current_user.id,
        post_type=req.post_type,
        title=req.title,
        content=req.content,
        vehicle_info=req.vehicle_info,
        component=req.component,
        audio_url=req.audio_url,
        memory_id=req.memory_id,
        report_id=req.report_id,
        is_public=req.is_public,
        tags=req.tags,
    )
    db.add(post)

    # 게시물 작성 소액 포인트
    await award_points(
        db, current_user.id, 10,
        PointTxType.DAILY_BONUS,
        f"커뮤니티 게시물 작성 보상",
        post_id, "community_post"
    )

    await db.commit()
    return {
        "success": True,
        "post_id": post_id,
        "points_awarded": 10,
        "message": "📢 게시물이 작성되었습니다!"
    }
