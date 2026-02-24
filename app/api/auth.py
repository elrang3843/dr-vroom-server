"""
Auth API — 사용자 인증 및 역할 관리
Client, Trainer, Expert 세 역할의 JWT 인증
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import Optional
import uuid
import hashlib
from datetime import datetime, timedelta
import hmac
import base64
import json

from app.db.database import get_db, User
from app.core.config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


# ─── Simple JWT (no external library dependency) ──────────────────────────────

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += '=' * padding
    return base64.urlsafe_b64decode(s)


def create_token(payload: dict) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url_encode(json.dumps(payload).encode())
    sig_input = f"{header}.{body}".encode()
    sig = hmac.new(settings.SECRET_KEY.encode(), sig_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url_encode(sig)}"


def verify_token(token: str) -> Optional[dict]:
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        header, body, sig = parts
        sig_input = f"{header}.{body}".encode()
        expected_sig = hmac.new(settings.SECRET_KEY.encode(), sig_input, hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_decode(sig), expected_sig):
            return None
        payload = json.loads(_b64url_decode(body))
        exp = payload.get("exp", 0)
        if datetime.utcnow().timestamp() > exp:
            return None
        return payload
    except Exception:
        return None


def hash_password(password: str) -> str:
    return hashlib.sha256(f"{password}{settings.SECRET_KEY}".encode()).hexdigest()


# ─── Request/Response Models ──────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., description="이메일")
    password: str = Field(..., min_length=6)
    role: str = Field("client", description="client/trainer/expert")
    device_id: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str
    device_id: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: str
    username: str
    expires_in: int


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """새 사용자 등록"""
    # Check username uniqueness
    result = await db.execute(select(User).where(User.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="이미 사용 중인 사용자명입니다.")

    # Validate role
    valid_roles = ["client", "trainer", "expert"]
    if req.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"역할은 {valid_roles} 중 하나여야 합니다.")

    user = User(
        id=str(uuid.uuid4()),
        username=req.username,
        email=req.email,
        hashed_password=hash_password(req.password),
        role=req.role,
        device_id=req.device_id,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    exp = (datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()
    token = create_token({
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "exp": exp,
    })

    return TokenResponse(
        access_token=token,
        role=user.role,
        user_id=user.id,
        username=user.username,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """로그인"""
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()

    if not user or user.hashed_password != hash_password(req.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자명 또는 비밀번호가 올바르지 않습니다.",
        )

    if not user.is_active:
        raise HTTPException(status_code=403, detail="비활성화된 계정입니다.")

    # Update device and last active
    user.device_id = req.device_id or user.device_id
    user.last_active = datetime.utcnow()

    exp = (datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()
    token = create_token({
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "exp": exp,
    })

    return TokenResponse(
        access_token=token,
        role=user.role,
        user_id=user.id,
        username=user.username,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/me")
async def get_me(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    """내 프로필 조회"""
    if not credentials:
        raise HTTPException(status_code=401, detail="인증 토큰이 필요합니다.")

    payload = verify_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")

    result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    return {
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "total_diagnoses": user.total_diagnoses,
        "knowledge_contributed": user.knowledge_contributed,
        "created_at": user.created_at.isoformat(),
        "last_active": user.last_active.isoformat(),
    }


# ─── Token verification helper for other APIs ─────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    if not credentials:
        raise HTTPException(status_code=401, detail="인증 토큰이 필요합니다.")
    payload = verify_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")
    result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    return user
