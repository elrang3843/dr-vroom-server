"""
WebSocket API — 실시간 스트리밍 진단
최대 1023 동시 접속
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import json
import asyncio

from app.db.database import get_db
from app.services.ws_manager import ws_manager
from app.services.brain import DrVroomBrain
from app.services.knowledge_service import KnowledgeService
from app.core.config import settings

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/{role}/{client_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    role: str,
    client_id: str,
):
    """
    실시간 WebSocket 연결
    role: client / trainer / expert
    """
    if role not in ["client", "trainer", "expert"]:
        await websocket.close(code=1008, reason="Invalid role")
        return

    connected = await ws_manager.connect(websocket, client_id, role)
    if not connected:
        return

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws_manager.send_to_client(client_id, role, {
                    "type": "error",
                    "message": "Invalid JSON",
                })
                continue

            msg_type = msg.get("type", "")

            # ─── 실시간 오디오 청크 분석 ─────────────────────────────────
            if msg_type == "audio_chunk":
                samples = msg.get("samples", [])
                if samples:
                    features = DrVroomBrain.extract_features(samples)
                    await ws_manager.send_to_client(client_id, role, {
                        "type": "realtime_features",
                        "rms": round(features.get("rms", 0), 4),
                        "dominant_freq": round(features.get("dominant_freq", 0), 1),
                        "waveform_sample": samples[::10][:50],
                    })

            # ─── Ping/Pong ────────────────────────────────────────────────
            elif msg_type == "pong":
                pass

            # ─── 서버 상태 요청 ───────────────────────────────────────────
            elif msg_type == "status":
                await ws_manager.send_to_client(client_id, role, {
                    "type": "server_status",
                    "connections": ws_manager.connection_counts,
                    "total": ws_manager.total_connections,
                    "max": settings.MAX_CONNECTIONS,
                    "version": settings.VERSION,
                })

            # ─── 트레이너: 지식 브로드캐스트 ─────────────────────────────
            elif msg_type == "broadcast_knowledge" and role in ["trainer", "expert"]:
                await ws_manager.broadcast_knowledge_update(msg.get("data", {}))

    except WebSocketDisconnect:
        await ws_manager.disconnect(client_id, role)
    except Exception as e:
        await ws_manager.disconnect(client_id, role)
