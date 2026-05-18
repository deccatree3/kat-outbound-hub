"""채널 공용 일괄 다운로드 헬퍼.

여러 결과 파일을 ① ZIP 1개 또는 ② 개별 N개 동시 트리거로 내려받게 한다.
로켓그로스 물류센터 전달 파일 탭에서 검증된 패턴을 공용화한 것.
(rocketgrowth/_tab_dispatch.py 의 사설 사본은 회귀 방지를 위해 유지.)
"""
from __future__ import annotations

import base64
import io
import json
import zipfile

import streamlit.components.v1 as components


def build_zip(items: list[tuple[str, bytes]], folder: str = "") -> bytes:
    """(filename, bytes) 리스트를 ZIP 으로 묶음. folder 지정 시 내부 폴더 생성."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname, content in items:
            if not content:
                continue
            arcname = f"{folder.rstrip('/')}/{fname}" if folder else fname
            zf.writestr(arcname, content)
    return buf.getvalue()


def render_zip_download_blue(zip_bytes: bytes, fname: str, label: str, key: str):
    """ZIP 다운로드를 파란색 커스텀 버튼으로 렌더."""
    b64 = base64.b64encode(zip_bytes).decode('ascii')
    html = f"""
<a href="data:application/zip;base64,{b64}" download="{fname}" id="zip-dl-{key}"
   style="
     display:inline-block; width:100%; padding:0.5rem 1rem;
     background:#1976d2; color:white; border:1px solid #1976d2; border-radius:0.5rem;
     text-align:center; font-weight:600; font-size:14px;
     text-decoration:none; cursor:pointer; box-sizing:border-box;
   ">{label}</a>
"""
    components.html(html, height=50)


def render_multi_download_trigger(items: list[tuple[str, bytes]], label: str, key: str):
    """한 번의 클릭으로 모든 파일을 개별 다운로드 트리거. base64 + JS."""
    files_js = []
    for name, content in items:
        if not content:
            continue
        b64 = base64.b64encode(content).decode('ascii')
        files_js.append({'name': name, 'b64': b64})
    if not files_js:
        return
    files_json = json.dumps(files_js)
    button_id = f"multi-dl-{key}"
    html = f"""
<button id="{button_id}" style="
    width:100%; padding:0.5rem 1rem;
    background:#ff4b4b; color:white; border:none; border-radius:0.5rem;
    font-weight:600; font-size:14px; cursor:pointer;
">{label}</button>
<script>
(function() {{
    const files = {files_json};
    document.getElementById("{button_id}").onclick = function() {{
        files.forEach((f, i) => {{
            setTimeout(() => {{
                const a = document.createElement('a');
                a.href = 'data:application/octet-stream;base64,' + f.b64;
                a.download = f.name;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
            }}, i * 250);
        }});
    }};
}})();
</script>
"""
    components.html(html, height=50)
