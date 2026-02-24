"""
Dr. Vroom (닥터브릉이) — Brain Server
메인 FastAPI 애플리케이션

Architecture:
  ┌─────────────────────────────────────────────────────────┐
  │                  Dr. Vroom Server                       │
  │  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
  │  │  Client App │  │ Trainer App  │  │  Expert App   │  │
  │  │ (진단 수행)  │  │(지식 교육)   │  │ (전문가 검증)  │  │
  │  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘  │
  │         │                │                   │          │
  │  ┌──────▼────────────────▼───────────────────▼───────┐  │
  │  │              REST API + WebSocket                  │  │
  │  └─────────────────────────┬──────────────────────────┘  │
  │                            │                             │
  │  ┌─────────────────────────▼──────────────────────────┐  │
  │  │          DrVroomBrain (AI 진단 엔진)                 │  │
  │  │   FFT Analysis + Knowledge DB + Learning System    │  │
  │  └──────────────────────────────────────────────────┘  │
  └─────────────────────────────────────────────────────────┘
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import asyncio

from app.core.config import settings
from app.db.database import init_db
from app.api import diagnosis, knowledge, websocket, auth
from app.services.ws_manager import ws_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 이벤트"""
    # 시작 시
    await init_db()
    print(f"🧠 {settings.APP_NAME_KR} ({settings.APP_NAME}) v{settings.VERSION}")
    print(f"📡 Patent: {settings.PATENT_NO}")
    print(f"🔗 Max connections: {settings.MAX_CONNECTIONS}")
    print(f"💾 Database: {settings.DATABASE_URL}")

    # WebSocket 하트비트 시작
    heartbeat_task = asyncio.create_task(ws_manager.heartbeat())

    yield

    # 종료 시
    heartbeat_task.cancel()
    print("👋 닥터브릉이 서버가 종료됩니다...")


# ─── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Dr. Vroom Brain Server",
    description="""
## 🚗 닥터브릉이 (Dr. Vroom) — 차량 진동 고장 진단 서버

**특허**: US 12,349,291 B2 — Fault Diagnostic Apparatus Using Microphone

### 3가지 앱 역할:
- 🟢 **Client App**: 차량 진단 수행, 결과 조회
- 🟡 **Trainer App**: 지식 교육, 레이블 부여, 데이터 검토
- 🔴 **Expert App**: 전문가 검증, 지식 승인, 시스템 관리

### 핵심 기능:
- 마이크를 통한 진동→음향 변환 (특허 기술)
- FFT 주파수 분석 (6개 부품)
- 경험 기반 자동 학습 (지식 누적)
- 최대 1,023명 동시 접속 WebSocket
    """,
    version=settings.VERSION,
    lifespan=lifespan,
)

# ─── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(diagnosis.router)
app.include_router(knowledge.router)
app.include_router(websocket.router)


# ─── Root ─────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "name": settings.APP_NAME,
        "name_kr": settings.APP_NAME_KR,
        "version": settings.VERSION,
        "patent": settings.PATENT_NO,
        "status": "alive",
        "message": "안녕하세요! 저는 닥터브릉이예요. 차량 소리를 들려주세요! 🚗",
        "endpoints": {
            "docs": "/docs",
            "diagnosis": "/api/v1/diagnosis/analyze",
            "knowledge": "/api/v1/knowledge/stats",
            "websocket": "/ws/{role}/{client_id}",
        },
        "connections": {
            "active": ws_manager.total_connections,
            "max": settings.MAX_CONNECTIONS,
            "by_role": ws_manager.connection_counts,
        },
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "connections": ws_manager.total_connections,
        "max_connections": settings.MAX_CONNECTIONS,
    }
