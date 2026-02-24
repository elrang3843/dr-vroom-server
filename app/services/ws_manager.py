"""
WebSocket Connection Manager
최대 1023명 동시 접속 관리
"""
from fastapi import WebSocket
from typing import Dict, Set
import json
import asyncio
from datetime import datetime
from app.core.config import settings


class ConnectionManager:
    """
    닥터브릉이의 신경망 — 모든 클라이언트와의 실시간 연결 관리
    Max: 1023 concurrent connections
    """

    def __init__(self):
        # role → {client_id: WebSocket}
        self.connections: Dict[str, Dict[str, WebSocket]] = {
            "client": {},
            "trainer": {},
            "expert": {},
        }
        self._lock = asyncio.Lock()

    @property
    def total_connections(self) -> int:
        return sum(len(v) for v in self.connections.values())

    @property
    def connection_counts(self) -> Dict[str, int]:
        return {role: len(conns) for role, conns in self.connections.items()}

    async def connect(self, websocket: WebSocket, client_id: str, role: str = "client") -> bool:
        """새 연결 등록"""
        if self.total_connections >= settings.MAX_CONNECTIONS:
            await websocket.close(code=1013, reason="Server at capacity (1023 max)")
            return False

        await websocket.accept()
        async with self._lock:
            if role not in self.connections:
                self.connections[role] = {}
            self.connections[role][client_id] = websocket

        # 연결 환영 메시지
        await self.send_to_client(client_id, role, {
            "type": "connected",
            "message": f"안녕하세요! 닥터브릉이 서버에 연결되었습니다.",
            "server_version": settings.VERSION,
            "total_connections": self.total_connections,
            "timestamp": datetime.utcnow().isoformat(),
        })
        return True

    async def disconnect(self, client_id: str, role: str = "client"):
        """연결 해제"""
        async with self._lock:
            if role in self.connections:
                self.connections[role].pop(client_id, None)

    async def send_to_client(self, client_id: str, role: str, data: dict):
        """특정 클라이언트에 메시지 전송"""
        ws = self.connections.get(role, {}).get(client_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                await self.disconnect(client_id, role)

    async def broadcast_to_role(self, role: str, data: dict):
        """특정 역할 그룹에 브로드캐스트"""
        if role not in self.connections:
            return
        dead = []
        for client_id, ws in self.connections[role].items():
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(client_id)
        for cid in dead:
            await self.disconnect(cid, role)

    async def broadcast_knowledge_update(self, knowledge_data: dict):
        """새 지식 학습 시 모든 클라이언트에 알림"""
        msg = {
            "type": "knowledge_updated",
            "message": "닥터브릉이가 새로운 것을 배웠어요!",
            "data": knowledge_data,
            "timestamp": datetime.utcnow().isoformat(),
        }
        for role in self.connections:
            await self.broadcast_to_role(role, msg)

    async def send_diagnosis_result(self, client_id: str, role: str, result: dict):
        """진단 결과 실시간 전송"""
        await self.send_to_client(client_id, role, {
            "type": "diagnosis_result",
            "data": result,
            "timestamp": datetime.utcnow().isoformat(),
        })

    async def heartbeat(self):
        """주기적 연결 상태 확인"""
        while True:
            await asyncio.sleep(settings.WS_HEARTBEAT_INTERVAL)
            dead_conns = []
            for role, conns in self.connections.items():
                for client_id, ws in conns.items():
                    try:
                        await ws.send_json({"type": "ping", "ts": datetime.utcnow().isoformat()})
                    except Exception:
                        dead_conns.append((client_id, role))
            for client_id, role in dead_conns:
                await self.disconnect(client_id, role)


# 싱글톤 인스턴스
ws_manager = ConnectionManager()
