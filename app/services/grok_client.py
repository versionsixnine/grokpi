"""Grok Imagine Image Generator - Menggunakan koneksi langsung WebSocket, mendukung preview streaming dan HTTP proxy"""

import asyncio
import json
import uuid
import time
import base64
import ssl
import re
from typing import Optional, List, Dict, Any, Callable, Awaitable
from dataclasses import dataclass, field

import aiohttp
from aiohttp_socks import ProxyConnector

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    curl_requests = None

from app.core.config import settings
from app.core.logger import logger

# Pilih SSO manager berdasarkan konfigurasi
if settings.REDIS_ENABLED:
    from app.services.redis_sso_manager import create_sso_manager
    sso_manager = create_sso_manager(
        use_redis=True,
        redis_url=settings.REDIS_URL,
        strategy=settings.SSO_ROTATION_STRATEGY,
        daily_limit=settings.SSO_DAILY_LIMIT
    )
else:
    from app.services.sso_manager import sso_manager


@dataclass
class ImageProgress:
    """Progress pembuatan untuk satu gambar"""
    image_id: str  # UUID yang diekstrak dari URL
    stage: str = "preview"  # preview -> medium -> final
    blob: str = ""
    blob_size: int = 0
    url: str = ""
    is_final: bool = False


@dataclass
class GenerationProgress:
    """Progress generate keseluruhan"""
    total: int = 4  # Jumlah yang diharapkan
    images: Dict[str, ImageProgress] = field(default_factory=dict)
    completed: int = 0  # Jumlah gambar final yang selesai
    has_medium: bool = False  # Apakah ada gambar tahap medium

    def get_completed_images(self) -> List[ImageProgress]:
        """Mendapatkan semua gambar yang selesai"""
        return [img for img in self.images.values() if img.is_final]

    def check_blocked(self) -> bool:
        """Cek apakah diblokir (ada medium tapi tidak ada final)"""
        has_medium = any(img.stage == "medium" for img in self.images.values())
        has_final = any(img.is_final for img in self.images.values())
        return has_medium and not has_final


# Tipe callback streaming
StreamCallback = Callable[[ImageProgress, GenerationProgress], Awaitable[None]]


class GrokImagineClient:
    """Klien WebSocket Grok Imagine"""

    def __init__(self):
        self._ssl_context = ssl.create_default_context()
        # Untuk ekstrak ID gambar dari URL
        self._url_pattern = re.compile(r'/images/([a-f0-9-]+)\.(png|jpg)')

    def _get_connector(self) -> Optional[aiohttp.BaseConnector]:
        """Mendapatkan connector (mendukung proxy)"""
        proxy_url = settings.PROXY_URL or settings.HTTP_PROXY or settings.HTTPS_PROXY

        if proxy_url:
            logger.info(f"[Grok] Menggunakan proxy: {proxy_url}")
            # Mendukung proxy http/https/socks4/socks5
            return ProxyConnector.from_url(proxy_url, ssl=self._ssl_context)

        return aiohttp.TCPConnector(ssl=self._ssl_context)

    def _get_ws_headers(self, sso: str) -> Dict[str, str]:
        """Membangun header request WebSocket"""
        return {
            "Cookie": f"sso={sso}; sso-rw={sso}",
            "Origin": "https://grok.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    def _extract_image_id(self, url: str) -> Optional[str]:
        """Ekstrak ID gambar dari URL"""
        match = self._url_pattern.search(url)
        if match:
            return match.group(1)
        return None

    def _is_final_image(self, url: str, blob_size: int) -> bool:
        """Menentukan apakah gambar final HD"""        
        # Versi final adalah format .jpg, ukuran biasanya > 100KB
        return url.endswith('.jpg') and blob_size > 100000

    async def _verify_age(self, sso: str) -> bool:
        """Verifikasi usia - Menggunakan curl_cffi untuk mensimulasikan request browser"""
        if not CURL_CFFI_AVAILABLE:
            logger.warning("[Grok] curl_cffi tidak terinstal, lewati verifikasi usia")
            return False

        if not settings.CF_CLEARANCE:
            logger.warning("[Grok] CF_CLEARANCE tidak dikonfigurasi, lewati verifikasi usia")
            return False

        cookie_str = f"sso={sso}; sso-rw={sso}; cf_clearance={settings.CF_CLEARANCE}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Origin": "https://grok.com",
            "Referer": "https://grok.com/",
            "Accept": "*/*",
            "Cookie": cookie_str,
            "Content-Type": "application/json",
        }

        proxy = settings.PROXY_URL or settings.HTTP_PROXY or settings.HTTPS_PROXY

        logger.info("[Grok] Sedang melakukan verifikasi usia...")

        try:
            # Jalankan request curl_cffi sinkron di thread pool
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: curl_requests.post(
                    "https://grok.com/rest/auth/set-birth-date",
                    headers=headers,
                    json={"birthDate": "2001-01-01T16:00:00.000Z"},
                    impersonate="chrome133a",
                    proxy=proxy,
                    verify=False
                )
            )

            if resp.status_code == 200:
                logger.info(f"[Grok] Verifikasi usia berhasil (status code: {resp.status_code})")
                return True
            else:
                logger.warning(f"[Grok] Response verifikasi usia: {resp.status_code} - {resp.text[:200]}")
                return False

        except Exception as e:
            logger.error(f"[Grok] Verifikasi usia gagal: {e}")
            return False

    async def generate(
        self,
        prompt: str,
        aspect_ratio: str = "2:3",
        n: int = None,
        enable_nsfw: bool = True,
        sso: Optional[str] = None,
        max_retries: int = 5,
        stream_callback: Optional[StreamCallback] = None
    ) -> Dict[str, Any]:
        """
        Generate gambar

        Args:
            prompt: Prompt
            aspect_ratio: Rasio aspek (1:1, 2:3, 3:2)
            n: Jumlah yang dihasilkan, jika tidak ditentukan gunakan nilai default konfigurasi
            enable_nsfw: Apakah mengaktifkan NSFW
            sso: SSO yang ditentukan, jika tidak maka ambil dari pool
            max_retries: Jumlah retry maksimal (untuk rotasi SSO berbeda)
            stream_callback: Callback streaming, dipanggil setiap kali ada update gambar

        Returns:
            Hasil generate, berisi daftar URL gambar
        """        
        # Gunakan jumlah gambar default dari konfigurasi
        if n is None:
            n = settings.DEFAULT_IMAGE_COUNT

        logger.info(f"[Grok] Request generate {n} gambar (DEFAULT_IMAGE_COUNT={settings.DEFAULT_IMAGE_COUNT})")

        last_error = None
        blocked_retries = 0  # Hitung retry blocked
        max_blocked_retries = 3  # Maksimal retry blocked

        for attempt in range(max_retries):
            current_sso = sso if sso else await sso_manager.get_next_sso()

            if not current_sso:
                return {"success": False, "error": "Tidak ada SSO yang tersedia"}

            # Cek status verifikasi usia
            age_verified = await sso_manager.get_age_verified(current_sso)
            if age_verified == 0:
                logger.info(f"[Grok] SSO {current_sso[:20]}... belum diverifikasi usia, mulai verifikasi...")
                verify_success = await self._verify_age(current_sso)
                if verify_success:
                    await sso_manager.set_age_verified(current_sso, 1)
                else:
                    logger.warning(f"[Grok] SSO {current_sso[:20]}... verifikasi usia gagal, lanjutkan mencoba generate")

            try:
                result = await self._do_generate(
                    sso=current_sso,
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    n=n,
                    enable_nsfw=enable_nsfw,
                    stream_callback=stream_callback
                )

                if result.get("success"):
                    await sso_manager.mark_success(current_sso)
                    # Catat penggunaan (update statistik dalam mode Redis)
                    if hasattr(sso_manager, 'record_usage'):
                        await sso_manager.record_usage(current_sso)
                    return result

                error_code = result.get("error_code", "")

                # Cek apakah diblokir
                if error_code == "blocked":
                    blocked_retries += 1
                    logger.warning(
                        f"[Grok] Terdeteksi blocked, retry {blocked_retries}/{max_blocked_retries}"
                    )
                    await sso_manager.mark_failed(current_sso, "blocked - tidak dapat menghasilkan gambar final")

                    if blocked_retries >= max_blocked_retries:
                        return {
                            "success": False,
                            "error_code": "blocked",
                            "error": f"Berturut-turut {max_blocked_retries} kali diblokir, silakan coba lagi nanti"
                        }
                    # Jika SSO ditentukan maka tidak retry
                    if sso:
                        return result
                    continue

                if error_code in ["rate_limit_exceeded", "unauthorized"]:
                    await sso_manager.mark_failed(current_sso, result.get("error", ""))
                    last_error = result
                    if sso:
                        return result
                    logger.info(f"[Grok] Percobaan {attempt + 1}/{max_retries} gagal, ganti SSO...")
                    continue
                else:
                    return result

            except Exception as e:
                logger.error(f"[Grok] Generate gagal: {e}")
                await sso_manager.mark_failed(current_sso, str(e))
                last_error = {"success": False, "error": str(e)}
                if sso:
                    return last_error
                continue

        return last_error or {"success": False, "error": "Semua retry gagal"}

    async def _do_generate(
        self,
        sso: str,
        prompt: str,
        aspect_ratio: str,
        n: int,
        enable_nsfw: bool,
        stream_callback: Optional[StreamCallback] = None
    ) -> Dict[str, Any]:
        """Eksekusi generate"""
        request_id = str(uuid.uuid4())
        headers = self._get_ws_headers(sso)

        logger.info(f"[Grok] Koneksi WebSocket: {settings.GROK_WS_URL}")

        connector = self._get_connector()

        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.ws_connect(
                    settings.GROK_WS_URL,
                    headers=headers,
                    heartbeat=20,
                    receive_timeout=settings.GENERATION_TIMEOUT
                ) as ws:
                    # Kirim request generate
                    message = {
                        "type": "conversation.item.create",
                        "timestamp": int(time.time() * 1000),
                        "item": {
                            "type": "message",
                            "content": [{
                                "requestId": request_id,
                                "text": prompt,
                                "type": "input_text",
                                "properties": {
                                    "section_count": 0,
                                    "is_kids_mode": False,
                                    "enable_nsfw": enable_nsfw,
                                    "skip_upsampler": False,
                                    "is_initial": False,
                                    "aspect_ratio": aspect_ratio
                                }
                            }]
                        }
                    }

                    await ws.send_json(message)
                    logger.info(f"[Grok] Request terkirim: {prompt[:50]}...")

                    # Pelacakan progress
                    progress = GenerationProgress(total=n)
                    error_info = None
                    start_time = time.time()
                    last_activity = time.time()
                    medium_received_time = None  # Waktu menerima medium

                    while time.time() - start_time < settings.GENERATION_TIMEOUT:
                        try:
                            ws_msg = await asyncio.wait_for(ws.receive(), timeout=5.0)

                            if ws_msg.type == aiohttp.WSMsgType.TEXT:
                                last_activity = time.time()
                                msg = json.loads(ws_msg.data)
                                msg_type = msg.get("type")

                                if msg_type == "image":
                                    blob = msg.get("blob", "")
                                    url = msg.get("url", "")

                                    if blob and url:
                                        image_id = self._extract_image_id(url)
                                        if not image_id:
                                            continue

                                        blob_size = len(blob)
                                        is_final = self._is_final_image(url, blob_size)

                                        # Tentukan tahap
                                        if is_final:
                                            stage = "final"
                                        elif blob_size > 30000:
                                            stage = "medium"
                                            # Catat waktu menerima medium
                                            if medium_received_time is None:
                                                medium_received_time = time.time()
                                        else:
                                            stage = "preview"

                                        # Update atau buat image progress
                                        img_progress = ImageProgress(
                                            image_id=image_id,
                                            stage=stage,
                                            blob=blob,
                                            blob_size=blob_size,
                                            url=url,
                                            is_final=is_final
                                        )

                                        # Hanya update ke tahap lebih tinggi
                                        existing = progress.images.get(image_id)
                                        if not existing or (not existing.is_final):
                                            progress.images[image_id] = img_progress

                                            # Update hitungan selesai
                                            progress.completed = len([
                                                img for img in progress.images.values()
                                                if img.is_final
                                            ])

                                            logger.info(
                                                f"[Grok] Gambar {image_id[:8]}... "
                                                f"tahap={stage} ukuran={blob_size} "
                                                f"progress={progress.completed}/{n}"
                                            )

                                            # Panggil callback streaming
                                            if stream_callback:
                                                try:
                                                    await stream_callback(img_progress, progress)
                                                except Exception as e:
                                                    logger.warning(f"[Grok] Error callback streaming: {e}")

                                elif msg_type == "error":
                                    error_code = msg.get("err_code", "")
                                    error_msg = msg.get("err_msg", "")
                                    logger.warning(f"[Grok] Error: {error_code} - {error_msg}")
                                    error_info = {"error_code": error_code, "error": error_msg}

                                    if error_code == "rate_limit_exceeded":
                                        return {
                                            "success": False,
                                            "error_code": error_code,
                                            "error": error_msg
                                        }

                                # Cek apakah sudah terkumpul cukup gambar final
                                if progress.completed >= n:
                                    logger.info(f"[Grok] Sudah terkumpul {progress.completed} gambar final")
                                    break

                                # Cek apakah diblokir: ada medium tapi lebih dari 15 detik tidak ada final
                                if medium_received_time and progress.completed == 0:
                                    time_since_medium = time.time() - medium_received_time
                                    if time_since_medium > 15:
                                        logger.warning(
                                            f"[Grok] Terdeteksi blocked: setelah menerima medium "
                                            f"{time_since_medium:.1f}s masih tidak ada final"
                                        )
                                        return {
                                            "success": False,
                                            "error_code": "blocked",
                                            "error": "Generate diblokir, tidak dapat mendapatkan gambar final"
                                        }

                            elif ws_msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                logger.warning(f"[Grok] WebSocket ditutup atau error: {ws_msg.type}")
                                break

                        except asyncio.TimeoutError:
                            # Cek apakah diblokir
                            if medium_received_time and progress.completed == 0:
                                time_since_medium = time.time() - medium_received_time
                                if time_since_medium > 10:
                                    logger.warning(
                                        f"[Grok] Timeout terdeteksi blocked: setelah menerima medium "
                                        f"{time_since_medium:.1f}s masih tidak ada final"
                                    )
                                    return {
                                        "success": False,
                                        "error_code": "blocked",
                                        "error": "Generate diblokir, tidak dapat mendapatkan gambar final"
                                    }

                            # Jika sudah ada beberapa gambar final dan lebih dari 10 detik tidak ada pesan baru, anggap selesai
                            if progress.completed > 0 and time.time() - last_activity > 10:
                                logger.info(f"[Grok] Timeout, sudah terkumpul {progress.completed} gambar")
                                break
                            continue

                    # Simpan gambar final
                    result_urls, result_b64 = await self._save_final_images(progress, n)

                    if result_urls:
                        return {
                            "success": True,
                            "urls": result_urls,
                            "b64_list": result_b64,
                            "count": len(result_urls)
                        }
                    elif error_info:
                        return {"success": False, **error_info}
                    else:
                        # Cek apakah blocked
                        if progress.check_blocked():
                            return {
                                "success": False,
                                "error_code": "blocked",
                                "error": "Generate diblokir, tidak dapat mendapatkan gambar final"
                            }
                        return {"success": False, "error": "Tidak menerima data gambar"}

        except aiohttp.ClientError as e:
            logger.error(f"[Grok] Error koneksi: {e}")
            return {"success": False, "error": f"Koneksi gagal: {e}"}

    async def _save_final_images(
        self,
        progress: GenerationProgress,
        n: int
    ) -> tuple[List[str], List[str]]:
        """Simpan gambar final ke lokal, sekaligus kembalikan daftar URL dan daftar base64"""
        result_urls = []
        result_b64 = []
        settings.IMAGES_DIR.mkdir(parents=True, exist_ok=True)

        # Prioritas simpan versi final, jika tidak ada gunakan versi terbesar
        saved_ids = set()

        for img in sorted(
            progress.images.values(),
            key=lambda x: (x.is_final, x.blob_size),
            reverse=True
        ):
            if img.image_id in saved_ids:
                continue
            if len(saved_ids) >= n:
                break

            try:
                image_data = base64.b64decode(img.blob)

                # Tentukan ekstensi berdasarkan apakah versi final
                ext = "jpg" if img.is_final else "png"
                filename = f"{img.image_id}.{ext}"
                filepath = settings.IMAGES_DIR / filename

                with open(filepath, 'wb') as f:
                    f.write(image_data)

                url = f"{settings.get_base_url()}/images/{filename}"
                result_urls.append(url)
                result_b64.append(img.blob)
                saved_ids.add(img.image_id)

                logger.info(
                    f"[Grok] Simpan gambar: {filename} "
                    f"({len(image_data) / 1024:.1f}KB, {img.stage})"
                )

            except Exception as e:
                logger.error(f"[Grok] Gagal menyimpan gambar: {e}")

        return result_urls, result_b64

    async def generate_stream(
        self,
        prompt: str,
        aspect_ratio: str = "2:3",
        n: int = None,
        enable_nsfw: bool = True,
        sso: Optional[str] = None
    ):
        """
        Generate gambar secara streaming - Menggunakan async generator

        Yields:
            Dict berisi informasi progress gambar saat ini
        """        
        # Gunakan jumlah gambar default dari konfigurasi
        if n is None:
            n = settings.DEFAULT_IMAGE_COUNT

        queue: asyncio.Queue = asyncio.Queue()
        done = asyncio.Event()

        async def callback(img: ImageProgress, prog: GenerationProgress):
            await queue.put({
                "type": "progress",
                "image_id": img.image_id,
                "stage": img.stage,
                "blob_size": img.blob_size,
                "is_final": img.is_final,
                "completed": prog.completed,
                "total": prog.total
            })

        async def generate_task():
            result = await self.generate(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                n=n,
                enable_nsfw=enable_nsfw,
                sso=sso,
                stream_callback=callback
            )
            await queue.put({"type": "result", **result})
            done.set()

        task = asyncio.create_task(generate_task())

        try:
            while not done.is_set() or not queue.empty():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield item
                    if item.get("type") == "result":
                        break
                except asyncio.TimeoutError:
                    continue
        finally:
            if not task.done():
                task.cancel()


# Instance global
grok_client = GrokImagineClient()
