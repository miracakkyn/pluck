"""Server-Sent Events (SSE) — canlı ilerleme yayını.

Anlık görüntü (snapshot) tabanlı: her `EVENT_INTERVAL` saniyede tüm işlerin
güncel durumu yayınlanır. İlerleme doğası gereği "son değer" odaklı olduğu
için ara değer kaçması önemsizdir (bkz. DESIGN.md §9).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

from app import config


async def event_stream(
    get_snapshot: Callable[[], list[dict]],
) -> AsyncIterator[str]:
    """İş durumlarının anlık görüntüsünü periyodik SSE çerçeveleri olarak verir.

    `get_snapshot` her çağrıda güncel iş listesini döndürür. İstemci
    bağlantıyı kapattığında Starlette bu üreteci iptal eder; özel temizlik
    gerekmez.
    """
    while True:
        payload = json.dumps(get_snapshot(), ensure_ascii=False)
        yield f"data: {payload}\n\n"
        await asyncio.sleep(config.EVENT_INTERVAL)
