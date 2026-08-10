"""Python kod tabanından sembol çıkarıcı.

Bir repoyu gezip her fonksiyon, metot ve sınıfı ayrı bir parça (chunk) hâline
getirir. Metin bölme (fixed-size chunking) yerine AST kullanılıyor; böylece her
parça anlamlı bir bütün oluyor ve satır aralığı doğru kalıyor — cevaplarda
`dosya:satır` referansı verebilmenin ön şartı bu.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .models import Chunk, IndexStats

#: Taranmayacak dizinler. Bunlar kod tabanının kendisi değil, üretilmiş/çekilmiş dosyalar.
DEFAULT_EXCLUDES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "node_modules",
        "site-packages",
        "build",
        "dist",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".eggs",
        ".idea",
        ".vscode",
    }
)

_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def iter_python_files(root: Path, excludes: frozenset[str] = DEFAULT_EXCLUDES):
    """`root` altındaki .py dosyalarını, dışlanan dizinlere girmeden sırayla verir."""
    root = root.resolve()
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.name in excludes:
                continue
            if entry.is_symlink():  # döngüye girmemek için
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.suffix == ".py":
                yield entry


def _span(node: ast.AST) -> tuple[int, int]:
    """Düğümün kaynak satır aralığı. Dekoratörler de parçaya dahil edilir."""
    start = node.lineno
    for decorator in getattr(node, "decorator_list", []):
        start = min(start, decorator.lineno)
    end = getattr(node, "end_lineno", None) or node.lineno
    return start, end


def _slice(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end])


def _signature(node: ast.AST) -> str:
    """Gövdesiz imza satırı: `def create(self, order: Order) -> int:`."""
    if isinstance(node, _FUNC_TYPES):
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        args = ast.unparse(node.args)
        returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"{prefix} {node.name}({args}){returns}:"
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases]
        bases += [ast.unparse(k) for k in node.keywords]
        suffix = f"({', '.join(bases)})" if bases else ""
        return f"class {node.name}{suffix}:"
    return ""


def _decorators(node: ast.AST) -> list[str]:
    return [f"@{ast.unparse(d)}" for d in getattr(node, "decorator_list", [])]


def _context(path: str, name: str, kind: str, module_doc: str | None) -> str:
    """Parçanın önüne eklenen konum etiketi (contextual retrieval).

    Şimdilik tamamen deterministik: dosya yolu + nitelenmiş ad + modül
    açıklamasının ilk satırı. İleride bu etiketi LLM ile zenginleştirmek
    (parçanın ne işe yaradığını bir cümleyle anlatmak) planlanıyor.
    """
    header = f"{path} > {name} ({kind})"
    if module_doc:
        first_line = module_doc.strip().splitlines()[0].strip()
        if first_line:
            header += f"\nModül: {first_line}"
    return header


def _class_summary(node: ast.ClassDef, signature: str, docstring: str | None) -> str:
    """Sınıf parçasının metni: başlık + docstring + metot imzaları.

    Sınıfın tüm gövdesi alınmıyor; metotlar zaten ayrı parça olarak çıkıyor ve
    tekrar indekslemek hem maliyet hem de arama kalitesi açısından zarar veriyor.
    """
    parts = [signature]
    if docstring:
        parts.append(f'    """{docstring.strip()}"""')
    methods = [f"    {_signature(n)}" for n in node.body if isinstance(n, _FUNC_TYPES)]
    if methods:
        parts.append("    # metotlar:")
        parts.extend(methods)
    return "\n".join(parts)


def extract_from_source(source: str, rel_path: str) -> list[Chunk]:
    """Tek bir Python dosyasının kaynağından parçaları çıkarır."""
    tree = ast.parse(source)
    lines = source.splitlines()
    module_doc = ast.get_docstring(tree)
    chunks: list[Chunk] = []

    def make(node, name: str, kind: str, text: str, parent: str | None) -> Chunk:
        start, end = _span(node)
        return Chunk(
            source="code",
            kind=kind,
            path=rel_path,
            name=name,
            start_line=start,
            end_line=end,
            text=text,
            context=_context(rel_path, name, kind, module_doc),
            signature=_signature(node) or None,
            docstring=ast.get_docstring(node) if isinstance(node, (ast.ClassDef, *_FUNC_TYPES)) else None,
            parent=parent,
        )

    # 1) Modül başlığı: docstring + import'lar. Dosyanın ne iş yaptığını ve neye
    #    bağlı olduğunu tek parçada toplar; "bu proje hangi kütüphaneyi kullanıyor"
    #    tarzı sorular buradan cevaplanıyor.
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    included: list[ast.stmt] = []
    header_parts: list[str] = []
    if module_doc:
        header_parts.append(f'"""{module_doc.strip()}"""')
        included.append(tree.body[0])  # docstring düğümü her zaman ilk sırada
    header_parts.extend(ast.unparse(n) for n in imports)
    included.extend(imports)
    if header_parts:
        module_name = rel_path.removesuffix(".py").replace("/", ".")
        # Modül parçasının metni birleştirilerek üretiliyor (docstring + import'lar),
        # yani kaynağın birebir kopyası değil. Satır aralığı yine de gerçek koda
        # denk gelsin diye ilk/son dahil edilen düğümden hesaplanıyor.
        start_line = min(n.lineno for n in included)
        end_line = max(n.end_lineno or n.lineno for n in included)
        chunks.append(
            Chunk(
                source="code",
                kind="module",
                path=rel_path,
                name=module_name,
                start_line=start_line,
                end_line=end_line,
                text="\n".join(header_parts),
                context=_context(rel_path, module_name, "module", module_doc),
                docstring=module_doc,
            )
        )

    def visit_body(body: list[ast.stmt], parent: str | None) -> None:
        for node in body:
            if isinstance(node, _FUNC_TYPES):
                name = f"{parent}.{node.name}" if parent else node.name
                kind = "method" if parent else "function"
                start, end = _span(node)
                chunks.append(make(node, name, kind, _slice(lines, start, end), parent))
                # İç içe fonksiyonlar ayrı parça değil; dış fonksiyonun gövdesinde
                # zaten yer alıyorlar.
            elif isinstance(node, ast.ClassDef):
                name = f"{parent}.{node.name}" if parent else node.name
                signature = _signature(node)
                docstring = ast.get_docstring(node)
                chunks.append(
                    make(node, name, "class", _class_summary(node, signature, docstring), parent)
                )
                visit_body(node.body, name)

    visit_body(tree.body, None)
    return chunks


def index_file(path: Path, root: Path) -> list[Chunk]:
    """Tek dosyayı indeksler. Yol, repo köküne göre göreli tutulur."""
    rel_path = path.resolve().relative_to(root.resolve()).as_posix()
    source = path.read_bytes().decode("utf-8", errors="replace")
    return extract_from_source(source, rel_path)


def index_repo(
    root: Path, excludes: frozenset[str] = DEFAULT_EXCLUDES
) -> tuple[list[Chunk], IndexStats]:
    """Bir repoyu baştan sona indeksler; parçaları ve koşu özetini döner."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Dizin bulunamadı: {root}")

    chunks: list[Chunk] = []
    stats = IndexStats()
    for file_path in iter_python_files(root, excludes):
        stats.files_scanned += 1
        try:
            file_chunks = index_file(file_path, root)
        except SyntaxError as exc:
            # Ayrıştırılamayan dosya koşuyu durdurmaz; farklı Python sürümüyle
            # yazılmış ya da bozuk dosyalar her repoda çıkıyor.
            stats.files_failed += 1
            rel = file_path.relative_to(root).as_posix()
            stats.errors.append(f"{rel}: sözdizimi hatası satır {exc.lineno}")
            continue
        except (OSError, ValueError) as exc:
            stats.files_failed += 1
            rel = file_path.relative_to(root).as_posix()
            stats.errors.append(f"{rel}: {exc}")
            continue
        for chunk in file_chunks:
            stats.add(chunk)
        chunks.extend(file_chunks)
    return chunks, stats
