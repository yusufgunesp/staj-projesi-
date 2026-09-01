"""DEMO.md'deki komutları düz metne çıkarır.

Sebebi bir provada ortaya çıktı: senaryo TextEdit'te ham markdown olarak
açılıyor ve kod bloğunun ``` tırnakları da kopyalanıyor. zsh ters tırnağı
komut ikamesi sayıp içine `bash` çalıştırıyor — kullanıcı iç içe bir kabuğa
düşüyor ve sonraki komutların hiçbiri koşmuyor, hata da vermiyor.

Çıktı `DEMO_KOMUTLAR.txt`. DEMO.md değişince yeniden çalıştırılmalı; testler
ikisinin eşitliğini kontrol ediyor.
"""

from __future__ import annotations

import re
from pathlib import Path

KAYNAK = Path(__file__).resolve().parent.parent / "DEMO.md"
HEDEF = Path(__file__).resolve().parent.parent / "DEMO_KOMUTLAR.txt"


def uret(markdown: str) -> str:
    satirlar, bolum = [], "Hazırlık"
    for blok in re.split(r"(^#{2,3} .+$)", markdown, flags=re.M):
        if blok.startswith("#"):
            bolum = blok.lstrip("# ").strip()
            continue
        for m in re.finditer(r"```bash\n(.*?)```", blok, re.S):
            for komut in m.group(1).strip().splitlines():
                satirlar.append((bolum, komut))

    out = [
        "# codeqa demo — komutlar",
        "# DEMO.md'den üretildi. Buradan kopyala: tırnak yok, doğrudan yapıştırılır.",
        "# Yeniden üretmek için:  python3 tools/komutlari_cikar.py",
        "",
    ]
    son = None
    for bolum, komut in satirlar:
        if bolum != son:
            out.append(f"# ── {bolum}")
            son = bolum
        out += [komut, ""]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    HEDEF.write_text(uret(KAYNAK.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"{HEDEF} yazıldı")
