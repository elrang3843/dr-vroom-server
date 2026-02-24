"""
닥터브릉이 커뮤니티 & 메모리 스키마
Dr. Vroom — Community, Memory & Gratitude Schema

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
설계 철학:
  🧠 BRAIN MEMORY  — 모든 지식을 한 곳에 정돈
  🙏 GRATITUDE     — 도움 준 친구에게 감사 표현
  👥 COMMUNITY     — 친구들 모이는 방법
  🔊 SOUND REPORT  — 정상/고장 소리 + 정보 제보
  🔗 FRIEND LINK   — 친구끼리 서로 도움 연결

법적 안전장치:
  ① 포인트 전용 — 현금 아님, 환율 불필요
  ② 감사 포인트 — '앱 내 가상 재화' 선물 (법적 부담 없음)
  ③ 친구 연결   — 소셜 그래프, 개인정보 최소 수집
  ④ 제보 보상   — 데이터 기여 대가의 포인트 지급
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Text,
    Boolean, JSON, ForeignKey, Enum as SAEnum, BigInteger
)
from datetime import datetime
import enum

from app.db.database import Base


# ═══════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════

class MemoryTag(str, enum.Enum):
    """브레인 메모리 태그 — 지식 분류"""
    NORMAL_SOUND   = "normal_sound"    # ✅ 정상 소리
    FAULT_SOUND    = "fault_sound"     # ❌ 고장 소리
    WARNING_SOUND  = "warning_sound"   # ⚠️ 경고 소리
    VEHICLE_INFO   = "vehicle_info"    # 🚗 차량 정보
    REPAIR_TIP     = "repair_tip"      # 🔧 수리 팁
    EXPERT_NOTE    = "expert_note"     # 👨‍🔧 전문가 메모
    USER_REPORT    = "user_report"     # 📝 사용자 제보
    VERIFIED       = "verified"        # ✅ 검증 완료

class MemoryStatus(str, enum.Enum):
    """메모리 상태"""
    PENDING    = "pending"    # 검토 대기
    ACTIVE     = "active"     # 활성 (학습에 사용 중)
    ARCHIVED   = "archived"   # 보관 (오래된 데이터)
    REJECTED   = "rejected"   # 거부 (품질 미달)

class FriendStatus(str, enum.Enum):
    """친구 관계 상태"""
    PENDING    = "pending"    # 요청 대기
    ACCEPTED   = "accepted"   # 수락됨
    BLOCKED    = "blocked"    # 차단됨

class GratitudeType(str, enum.Enum):
    """감사 표현 유형"""
    THANKS_POINTS  = "thanks_points"    # 포인트 선물 (감사 표현)
    STAR_RATING    = "star_rating"      # 별점 평가
    TESTIMONIAL    = "testimonial"      # 후기 작성
    RECOMMEND      = "recommend"        # 추천인 등록
    HELPFUL_VOTE   = "helpful_vote"     # '도움됨' 투표

class SoundReportType(str, enum.Enum):
    """소리 제보 유형"""
    NORMAL         = "normal"       # 정상 소리 제보
    ABNORMAL       = "abnormal"     # 비정상/고장 소리 제보
    BEFORE_REPAIR  = "before_repair"  # 수리 전 소리
    AFTER_REPAIR   = "after_repair"   # 수리 후 소리 (검증용)
    UNKNOWN        = "unknown"      # 모르겠음

class ConnectionReason(str, enum.Enum):
    """친구 연결 이유 (같은 문제를 가진 사람들 연결)"""
    SAME_VEHICLE    = "same_vehicle"    # 같은 차종
    SAME_SYMPTOM    = "same_symptom"    # 같은 증상
    SAME_COMPONENT  = "same_component"  # 같은 부품 문제
    REFERRAL        = "referral"        # 초대로 연결
    BOUNTY_MATCH    = "bounty_match"    # 현상금 게시물로 연결
    EXPERT_INTRO    = "expert_intro"    # 전문가 소개

class NotificationChannel(str, enum.Enum):
    """알림 채널"""
    IN_APP   = "in_app"    # 앱 내 알림
    PUSH     = "push"      # 푸시 알림
    EMAIL    = "email"     # 이메일


# ═══════════════════════════════════════════════════
# 1. BRAIN MEMORY — 브레인 메모리 (중앙 지식 저장소)
# ═══════════════════════════════════════════════════

class BrainMemory(Base):
    """
    🧠 닥터브릉이의 중앙 기억 저장소
    모든 지식을 한 곳에서 정돈하고 관리

    - sound_knowledge 테이블의 상위 계층
    - 태그, 상태, 기여자 추적 포함
    - 검색 최적화 (전문 검색 지원)
    """
    __tablename__ = "brain_memories"

    id              = Column(String, primary_key=True)

    # 지식 내용
    memory_type     = Column(SAEnum(MemoryTag), index=True)
    title           = Column(String(200))              # 기억 제목
    content         = Column(Text)                     # 핵심 내용 (자연어)
    structured_data = Column(JSON, default={})         # 구조화 데이터 (FFT 등)

    # 차량 연관성
    vehicle_type    = Column(String, index=True, nullable=True)
    vehicle_brand   = Column(String, index=True, nullable=True)
    vehicle_model   = Column(String, nullable=True)
    component       = Column(String, index=True, nullable=True)  # 어느 부품?

    # 소리 특성 (있는 경우)
    dominant_freq   = Column(Float, nullable=True)
    freq_signature  = Column(JSON, nullable=True)      # 주파수 서명
    audio_url       = Column(String, nullable=True)    # 녹음 파일 URL

    # 출처 및 신뢰도
    source_user_id  = Column(String, ForeignKey("users.id"), index=True, nullable=True)
    source_type     = Column(String, default="user_report")  # client/trainer/expert/system
    confidence      = Column(Float, default=0.5)
    verification_count = Column(Integer, default=0)   # 검증 횟수
    helpful_count   = Column(Integer, default=0)       # '도움됨' 투표 수

    # 상태
    status          = Column(SAEnum(MemoryStatus), default=MemoryStatus.PENDING, index=True)
    is_public       = Column(Boolean, default=True)    # 공개 여부
    priority        = Column(Integer, default=0)       # 우선순위 (높을수록 먼저 노출)

    # 태그 (다중 태그 지원)
    tags            = Column(JSON, default=[])         # ["engine", "normal", "toyota"]

    # 연관된 knowledge_id (sound_knowledge 테이블)
    knowledge_id    = Column(String, nullable=True, index=True)
    # 연관된 diagnosis_session_id
    session_id      = Column(String, nullable=True, index=True)

    # 보상 처리
    points_awarded  = Column(Integer, default=0)       # 기여 포인트 지급액
    points_paid_at  = Column(DateTime, nullable=True)

    # 시간
    created_at      = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MemoryVerification(Base):
    """
    🔍 메모리 검증 기록
    전문가/다른 사용자가 기억을 검증하면 신뢰도 상승
    """
    __tablename__ = "memory_verifications"

    id              = Column(String, primary_key=True)
    memory_id       = Column(String, ForeignKey("brain_memories.id"), index=True)
    verifier_id     = Column(String, ForeignKey("users.id"), index=True)
    verifier_role   = Column(String)                   # client/trainer/expert
    is_correct      = Column(Boolean)                  # 맞음/틀림
    correction_note = Column(Text, nullable=True)      # 수정 의견
    points_earned   = Column(Integer, default=0)       # 검증 포인트 보상
    created_at      = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════
# 2. SOUND REPORT — 소리 제보 시스템
# ═══════════════════════════════════════════════════

class SoundReport(Base):
    """
    🔊 소리 제보
    친구들이 정상/고장 소리와 정보를 알려주는 시스템

    - 제보한 소리는 BrainMemory로 변환됨
    - 채택된 제보에 포인트 지급
    - 전문가 검증 후 공식 지식으로 승격
    """
    __tablename__ = "sound_reports"

    id              = Column(String, primary_key=True)
    reporter_id     = Column(String, ForeignKey("users.id"), index=True)

    # 제보 내용
    report_type     = Column(SAEnum(SoundReportType), index=True)
    title           = Column(String(200))
    description     = Column(Text)                     # 자연어 설명

    # 차량 정보
    vehicle_type    = Column(String, nullable=True)
    vehicle_brand   = Column(String, nullable=True)
    vehicle_model   = Column(String, nullable=True)
    vehicle_year    = Column(Integer, nullable=True)
    mileage_km      = Column(Integer, nullable=True)

    # 소리 특성 (직접 측정)
    component       = Column(String, nullable=True)    # 어느 부품인지 알면
    audio_data      = Column(JSON, nullable=True)      # 원시 파형 또는 FFT 결과
    audio_url       = Column(String, nullable=True)    # 녹음 파일 URL
    dominant_freq   = Column(Float, nullable=True)
    rms_amplitude   = Column(Float, nullable=True)
    freq_signature  = Column(JSON, nullable=True)

    # 상황 정보
    when_does_it_happen = Column(Text, nullable=True)  # "엑셀 밟을 때", "저속에서" 등
    repair_history  = Column(Text, nullable=True)      # 수리 이력
    is_repaired     = Column(Boolean, default=False)   # 수리 완료 여부
    repair_result   = Column(Text, nullable=True)      # 수리 후 결과

    # 처리 상태
    status          = Column(String, default="submitted")  # submitted/reviewing/accepted/rejected
    review_note     = Column(Text, nullable=True)
    memory_id       = Column(String, nullable=True)    # 변환된 BrainMemory ID

    # 보상
    points_awarded  = Column(Integer, default=0)
    is_featured     = Column(Boolean, default=False)   # 주요 제보 (추가 보상)

    # 통계
    helpful_votes   = Column(Integer, default=0)
    view_count      = Column(Integer, default=0)

    # 태그
    tags            = Column(JSON, default=[])

    created_at      = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ═══════════════════════════════════════════════════
# 3. FRIEND SYSTEM — 친구 시스템
# ═══════════════════════════════════════════════════

class Friend(Base):
    """
    👥 친구 관계
    - 직접 친구 요청
    - 초대 코드로 연결
    - 같은 문제/차량으로 자동 매칭
    """
    __tablename__ = "friends"

    id              = Column(String, primary_key=True)
    requester_id    = Column(String, ForeignKey("users.id"), index=True)
    receiver_id     = Column(String, ForeignKey("users.id"), index=True)
    status          = Column(SAEnum(FriendStatus), default=FriendStatus.PENDING, index=True)
    connection_reason = Column(SAEnum(ConnectionReason), nullable=True)

    # 연결 맥락
    context_data    = Column(JSON, default={})  # 어떤 차량/증상으로 연결됐나
    message         = Column(String(300), nullable=True)  # 친구 요청 메시지

    # 통계
    shared_diagnoses = Column(Integer, default=0)  # 함께 공유한 진단 수
    helped_count    = Column(Integer, default=0)    # 서로 도움 준 횟수

    created_at      = Column(DateTime, default=datetime.utcnow)
    accepted_at     = Column(DateTime, nullable=True)


class FriendHelp(Base):
    """
    🤝 친구 도움 기록
    친구가 나의 문제 해결에 도움을 줬을 때 기록
    → 감사 포인트 지급 근거
    """
    __tablename__ = "friend_helps"

    id              = Column(String, primary_key=True)
    helper_id       = Column(String, ForeignKey("users.id"), index=True)   # 도움 준 사람
    helped_id       = Column(String, ForeignKey("users.id"), index=True)   # 도움 받은 사람
    help_type       = Column(String)  # "sound_share"/"diagnosis_help"/"bounty_answer"/"info_share"
    ref_id          = Column(String, nullable=True)  # 관련 게시물 ID
    description     = Column(Text, nullable=True)

    # 보상
    gratitude_points_sent = Column(Integer, default=0)  # 받은 사람이 보낸 감사 포인트
    thank_message   = Column(String(300), nullable=True)

    created_at      = Column(DateTime, default=datetime.utcnow)


class InviteCode(Base):
    """
    📨 초대 코드
    - 친구 초대 시 둘 다 보너스 포인트
    - 초대받은 사람이 첫 진단 완료 시 지급
    - 최대 초대 인원 제한 없음 (단, 어뷰징 감지)
    """
    __tablename__ = "invite_codes"

    id              = Column(String, primary_key=True)  # 초대 코드 자체
    owner_id        = Column(String, ForeignKey("users.id"), index=True)
    owner_bonus     = Column(Integer, default=100)      # 초대 성공 시 오너 보너스
    invitee_bonus   = Column(Integer, default=50)       # 초대받은 사람 보너스

    use_count       = Column(Integer, default=0)        # 사용 횟수
    max_uses        = Column(Integer, default=100)      # 최대 사용 횟수
    is_active       = Column(Boolean, default=True)

    created_at      = Column(DateTime, default=datetime.utcnow)
    expires_at      = Column(DateTime, nullable=True)


class InviteUsage(Base):
    """초대 코드 사용 기록"""
    __tablename__ = "invite_usages"

    id              = Column(String, primary_key=True)
    code_id         = Column(String, ForeignKey("invite_codes.id"), index=True)
    invitee_id      = Column(String, ForeignKey("users.id"), index=True)
    is_completed    = Column(Boolean, default=False)  # 첫 진단 완료 여부
    bonus_paid      = Column(Boolean, default=False)
    created_at      = Column(DateTime, default=datetime.utcnow)
    completed_at    = Column(DateTime, nullable=True)


# ═══════════════════════════════════════════════════
# 4. GRATITUDE SYSTEM — 감사 시스템
# ═══════════════════════════════════════════════════

class Gratitude(Base):
    """
    🙏 감사 표현
    도움받은 친구에게 감사를 표현하는 다양한 방법

    법적 안전:
    - 포인트 선물은 '앱 내 가상 재화 증여'
    - 현금화 불가 → 전자금융거래법 무관
    - 글로벌 동일 → 외환거래법 무관
    """
    __tablename__ = "gratitudes"

    id              = Column(String, primary_key=True)
    sender_id       = Column(String, ForeignKey("users.id"), index=True)    # 감사 보내는 사람
    receiver_id     = Column(String, ForeignKey("users.id"), index=True)    # 감사 받는 사람
    gratitude_type  = Column(SAEnum(GratitudeType), index=True)

    # 포인트 선물인 경우
    points_amount   = Column(Integer, default=0)       # 선물 포인트
    message         = Column(String(500), nullable=True)  # 감사 메시지

    # 참조
    ref_id          = Column(String, nullable=True)    # 어떤 도움에 대한 감사인지
    ref_type        = Column(String, nullable=True)    # "bounty"/"sound_report"/"help" 등

    # 별점 (star_rating 유형)
    star_score      = Column(Integer, nullable=True)   # 1~5점

    # 처리 상태
    is_processed    = Column(Boolean, default=False)   # 포인트 이전 완료 여부

    created_at      = Column(DateTime, default=datetime.utcnow, index=True)


class ThankYouNote(Base):
    """
    💌 감사 노트
    - 공개 또는 비공개 감사 메시지
    - 커뮤니티 분위기 향상
    - 포인트 없이도 감사 표현 가능
    """
    __tablename__ = "thankyou_notes"

    id              = Column(String, primary_key=True)
    sender_id       = Column(String, ForeignKey("users.id"), index=True)
    receiver_id     = Column(String, ForeignKey("users.id"), index=True)
    message         = Column(Text)
    is_public       = Column(Boolean, default=True)    # 공개 감사 = 커뮤니티에 노출
    is_anonymous    = Column(Boolean, default=False)   # 익명 감사

    # 참조
    ref_id          = Column(String, nullable=True)
    ref_type        = Column(String, nullable=True)

    likes_count     = Column(Integer, default=0)       # 다른 사람들의 '좋아요'
    created_at      = Column(DateTime, default=datetime.utcnow, index=True)


# ═══════════════════════════════════════════════════
# 5. COMMUNITY FEED — 커뮤니티 피드
# ═══════════════════════════════════════════════════

class CommunityPost(Base):
    """
    📢 커뮤니티 게시물
    - 소리 공유, 수리 후기, 팁 공유 등
    - 친구들이 모이는 광장
    """
    __tablename__ = "community_posts"

    id              = Column(String, primary_key=True)
    author_id       = Column(String, ForeignKey("users.id"), index=True)

    post_type       = Column(String, index=True)  # "sound_share"/"repair_story"/"tip"/"question"/"warning"
    title           = Column(String(200))
    content         = Column(Text)

    # 차량 정보 (선택)
    vehicle_info    = Column(JSON, nullable=True)
    component       = Column(String, nullable=True)

    # 첨부 (소리 데이터)
    audio_url       = Column(String, nullable=True)
    memory_id       = Column(String, nullable=True)   # 연결된 BrainMemory
    report_id       = Column(String, nullable=True)   # 연결된 SoundReport

    # 공개 설정
    is_public       = Column(Boolean, default=True)
    visible_to_friends_only = Column(Boolean, default=False)

    # 통계
    view_count      = Column(Integer, default=0)
    like_count      = Column(Integer, default=0)
    comment_count   = Column(Integer, default=0)
    helpful_count   = Column(Integer, default=0)
    share_count     = Column(Integer, default=0)

    # 태그
    tags            = Column(JSON, default=[])
    location        = Column(String, nullable=True)  # 지역 정보 (선택)

    # 보상
    points_awarded  = Column(Integer, default=0)
    is_featured     = Column(Boolean, default=False)  # 주요 게시물

    created_at      = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PostComment(Base):
    """게시물 댓글"""
    __tablename__ = "post_comments"

    id              = Column(String, primary_key=True)
    post_id         = Column(String, ForeignKey("community_posts.id"), index=True)
    author_id       = Column(String, ForeignKey("users.id"), index=True)
    parent_id       = Column(String, nullable=True)    # 대댓글
    content         = Column(Text)
    is_expert_answer = Column(Boolean, default=False)  # 전문가 답변
    like_count      = Column(Integer, default=0)
    is_deleted      = Column(Boolean, default=False)
    created_at      = Column(DateTime, default=datetime.utcnow)


class PostLike(Base):
    """게시물/댓글 좋아요"""
    __tablename__ = "post_likes"

    id              = Column(String, primary_key=True)
    user_id         = Column(String, ForeignKey("users.id"), index=True)
    target_id       = Column(String, index=True)    # post_id or comment_id
    target_type     = Column(String)                # "post" or "comment"
    created_at      = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════
# 6. NOTIFICATION — 알림 시스템
# ═══════════════════════════════════════════════════

class Notification(Base):
    """
    🔔 알림
    - 친구 요청, 감사 수신, 새 답변, 소리 검증 결과
    - 앱 내 + 푸시 알림
    """
    __tablename__ = "notifications"

    id              = Column(String, primary_key=True)
    user_id         = Column(String, ForeignKey("users.id"), index=True)

    notif_type      = Column(String, index=True)
    # Types:
    # "friend_request"    — 친구 요청
    # "friend_accepted"   — 친구 수락
    # "gratitude_received"— 감사 포인트 수신
    # "sound_verified"    — 제보한 소리 검증됨
    # "bounty_answered"   — 내 현상금에 답변
    # "bounty_adopted"    — 내 답변이 채택됨
    # "points_earned"     — 포인트 적립
    # "memory_featured"   — 내 기억이 주요 기억으로 선정
    # "help_request"      — 친구가 도움 요청
    # "new_connection"    — 비슷한 문제를 가진 사람 연결

    title           = Column(String(200))
    body            = Column(Text)
    data            = Column(JSON, default={})      # 딥링크 등 부가 데이터

    # 참조
    ref_id          = Column(String, nullable=True)
    ref_type        = Column(String, nullable=True)

    is_read         = Column(Boolean, default=False, index=True)
    is_sent_push    = Column(Boolean, default=False)

    channel         = Column(SAEnum(NotificationChannel), default=NotificationChannel.IN_APP)
    created_at      = Column(DateTime, default=datetime.utcnow, index=True)
    read_at         = Column(DateTime, nullable=True)


# ═══════════════════════════════════════════════════
# 7. SAME_VEHICLE_GROUP — 같은 차 모임
# ═══════════════════════════════════════════════════

class VehicleGroup(Base):
    """
    🚗 같은 차종 모임
    동일 차종/연식 사용자들을 자동으로 연결
    - 같은 차 = 같은 문제 공유 가능
    - 집단지성으로 더 정확한 진단
    """
    __tablename__ = "vehicle_groups"

    id              = Column(String, primary_key=True)
    vehicle_type    = Column(String, index=True)
    vehicle_brand   = Column(String, index=True)
    vehicle_model   = Column(String, index=True)
    vehicle_year    = Column(Integer, nullable=True)

    group_name      = Column(String(100))     # "현대 아반떼 2020년식 모임"
    description     = Column(Text, nullable=True)
    member_count    = Column(Integer, default=0)
    post_count      = Column(Integer, default=0)
    is_active       = Column(Boolean, default=True)

    # 자동 생성된 공통 지식
    common_issues   = Column(JSON, default=[])  # 이 차 모임의 공통 문제들
    top_memories    = Column(JSON, default=[])  # 가장 유용한 BrainMemory IDs

    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VehicleGroupMember(Base):
    """차종 모임 멤버"""
    __tablename__ = "vehicle_group_members"

    id              = Column(String, primary_key=True)
    group_id        = Column(String, ForeignKey("vehicle_groups.id"), index=True)
    user_id         = Column(String, ForeignKey("users.id"), index=True)
    vehicle_info    = Column(JSON, default={})  # 사용자의 실제 차량 상세 정보
    role            = Column(String, default="member")  # member/moderator/admin
    joined_at       = Column(DateTime, default=datetime.utcnow)
