"""
Dr. Vroom Brain Database — SQLite (zero-cost, upgradeable)
닥터브릉이의 모든 기억과 지식을 저장하는 공간
"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Float, Integer, DateTime, Text, Boolean, JSON
from datetime import datetime
from app.core.config import settings


# ─── Engine ──────────────────────────────────────────────────────────────────
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


# ─── Models ───────────────────────────────────────────────────────────────────

class SoundKnowledge(Base):
    """
    닥터브릉이의 핵심 지식 DB
    "이 소리 패턴 = 이 부품의 이 상태" 를 기억하는 뇌세포
    """
    __tablename__ = "sound_knowledge"

    id = Column(String, primary_key=True)
    # 차량 정보
    vehicle_type = Column(String, index=True)        # sedan, suv, truck, etc.
    vehicle_brand = Column(String, index=True)       # toyota, hyundai, etc.
    vehicle_model = Column(String)
    engine_cc = Column(Integer)                      # engine displacement

    # 소리/진동 특성 (핵심 특징값)
    component = Column(String, index=True)           # engine, bearing, etc.
    dominant_freq = Column(Float)                    # Hz
    freq_band_energy = Column(Text)                  # JSON: {band: energy}
    rms_amplitude = Column(Float)
    peak_amplitude = Column(Float)
    spectral_centroid = Column(Float)
    zero_crossing_rate = Column(Float)

    # 진단 결과
    status = Column(String, index=True)              # normal/warning/critical
    fault_code = Column(String)
    description = Column(Text)

    # 학습 메타데이터
    confidence = Column(Float, default=0.5)
    sample_count = Column(Integer, default=1)        # 같은 패턴 학습 횟수
    confirmed_by_expert = Column(Boolean, default=False)
    source = Column(String, default="client")        # client/trainer/expert

    # 시간
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DiagnosisSession(Base):
    """
    모든 진단 세션 기록 — 닥터브릉이의 경험 일지
    """
    __tablename__ = "diagnosis_sessions"

    id = Column(String, primary_key=True)
    client_id = Column(String, index=True)          # 어떤 기기/사용자
    session_type = Column(String)                    # client / trainer / expert

    # 차량 정보
    vehicle_info = Column(String)
    vehicle_type = Column(String)

    # 분석 데이터 (요약만 저장 — 비용 절약)
    waveform_summary = Column(Text)                  # JSON: RMS, peak, dominant_freq
    frequency_peaks = Column(Text)                   # JSON: top 5 frequency peaks
    duration_seconds = Column(Integer)

    # 진단 결과
    component_results = Column(Text)                 # JSON array
    overall_status = Column(String)
    health_score = Column(Float)

    # 학습 여부
    contributed_to_knowledge = Column(Boolean, default=False)
    knowledge_ids = Column(Text)                     # JSON: list of knowledge IDs updated

    created_at = Column(DateTime, default=datetime.utcnow)


class TrainingLabel(Base):
    """
    전문가/트레이너가 부여한 정답 레이블
    닥터브릉이의 선생님 목소리
    """
    __tablename__ = "training_labels"

    id = Column(String, primary_key=True)
    session_id = Column(String, index=True)          # 어떤 세션에 대한 레이블
    trainer_id = Column(String)

    # 정답 레이블
    component = Column(String)
    correct_status = Column(String)
    correct_fault_code = Column(String)
    notes = Column(Text)

    # 오디오 특징 (트레이너가 직접 입력)
    freq_signature = Column(Text)                    # JSON
    tags = Column(Text)                              # JSON: ["knock", "bearing", ...]

    verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    """
    사용자 관리 (Client / Trainer / Expert)
    """
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True)
    hashed_password = Column(String)
    role = Column(String, default="client")          # client / trainer / expert
    is_active = Column(Boolean, default=True)
    device_id = Column(String)

    total_diagnoses = Column(Integer, default=0)
    knowledge_contributed = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)


class ServerStats(Base):
    """
    서버 통계 — 닥터브릉이의 성장 지표
    """
    __tablename__ = "server_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(DateTime, default=datetime.utcnow)
    total_knowledge_items = Column(Integer, default=0)
    total_diagnoses = Column(Integer, default=0)
    total_users = Column(Integer, default=0)
    active_connections = Column(Integer, default=0)
    avg_confidence = Column(Float, default=0.0)


# ─── Init DB ─────────────────────────────────────────────────────────────────

async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """Dependency for database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
