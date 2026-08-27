"""Kod tabanından sembol çıkarıcı.

Bir repoyu gezip her fonksiyon, metot ve sınıfı ayrı bir parça (chunk) hâline
getirir. Metin bölme (fixed-size chunking) yerine sözdizimi ağacı kullanılıyor;
böylece her parça anlamlı bir bütün oluyor ve satır aralığı doğru kalıyor —
cevaplarda `dosya:satır` referansı verebilmenin ön şartı bu.

Python yerleşik `ast` ile, diğer diller tree-sitter grameriyle ayrıştırılıyor
(bkz. `languages.py`). Desteklenen diller: Python, C, C++, Java, C#, Go,
TypeScript, JavaScript.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .languages import EXTENSION_MAP, spec_for
from .languages import extract_from_source as extract_with_grammar
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

#: Modül seviyesi bir atamanın parçaya girecek azami uzunluğu. Uzun __all__
#: listeleri ve gömülü veri tabloları arama değeri taşımadan parçayı şişiriyor.
MAX_ASSIGNMENT_CHARS = 300


#: İndekslenebilen tüm uzantılar: Python (yerleşik `ast`) + tree-sitter dilleri.
SOURCE_EXTENSIONS: frozenset[str] = frozenset({".py"}) | frozenset(EXTENSION_MAP)


def iter_source_files(root: Path, excludes: frozenset[str] = DEFAULT_EXCLUDES):
    """`root` altındaki kaynak dosyalarını, dışlanan dizinlere girmeden verir."""
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
            elif entry.suffix in SOURCE_EXTENSIONS:
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


def _attribute_doc(body: list[ast.stmt], index: int) -> str | None:
    """Bir atamanın hemen ardından gelen çıplak string: PEP 258 öznitelik docstring'i.

    `ast` bunları atamaya bağlamıyor — gövdede ayrı bir `Expr` düğümü olarak
    duruyorlar ve yalnızca `Import`/`Assign` düğümlerini toplayan bir okuyucudan
    sessizce düşüyorlar. Bu SDK'da kaybın ölçüsü: 1097 dosyanın 624'ünde toplam
    2025 docstring, 222.843 karakter. Üstelik tipli bir kütüphanede en açıklayıcı
    metin bunlar — API alanlarının ne işe yaradığını anlatan satırlar.
    """
    if index + 1 >= len(body):
        return None
    following = body[index + 1]
    if (
        isinstance(following, ast.Expr)
        and isinstance(following.value, ast.Constant)
        and isinstance(following.value.value, str)
    ):
        return following.value.value
    return None


def _assignment_text(node: ast.stmt, doc: str | None, indent: str = "") -> str:
    """Bir atamayı, varsa öznitelik docstring'iyle birlikte metne çevirir."""
    text = ast.unparse(node)
    if len(text) > MAX_ASSIGNMENT_CHARS:
        # Büyük veri yapıları (uzun __all__ listeleri, gömülü tablolar)
        # parçayı şişiriyor ve arama değeri taşımıyor.
        text = text[:MAX_ASSIGNMENT_CHARS] + "  # ... (kısaltıldı)"
    lines = [f"{indent}{text}"]
    if doc:
        lines.append(f'{indent}"""{doc.strip()}"""')
    return "\n".join(lines)


def _class_summary(node: ast.ClassDef, signature: str, docstring: str | None) -> str:
    """Sınıf parçasının metni: başlık + docstring + metot imzaları.

    Sınıfın tüm gövdesi alınmıyor; metotlar zaten ayrı parça olarak çıkıyor ve
    tekrar indekslemek hem maliyet hem de arama kalitesi açısından zarar veriyor.
    """
    parts = [signature]
    if docstring:
        parts.append(f'    """{docstring.strip()}"""')

    # Alanlar ve onların öznitelik docstring'leri. Bunlar olmadan bir TypedDict
    # parçası imza + docstring'ten ibaret kalıyordu; alanların ne anlama geldiğini
    # anlatan metin parçaya hiç girmiyordu.
    fields = []
    for index, child in enumerate(node.body):
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            fields.append(_assignment_text(child, _attribute_doc(node.body, index), indent="    "))
    if fields:
        parts.append("    # alanlar:")
        parts.extend(fields)

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
            docstring=ast.get_docstring(node)
            if isinstance(node, (ast.ClassDef, *_FUNC_TYPES))
            else None,
            parent=parent,
        )

    # 1) Modül başlığı: docstring + import'lar + modül seviyesi sabitler.
    #    Dosyanın ne iş yaptığını, neye bağlı olduğunu ve hangi varsayılan
    #    değerleri tanımladığını tek parçada toplar.
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    included: list[ast.stmt] = []
    header_parts: list[str] = []
    if module_doc:
        header_parts.append(f'"""{module_doc.strip()}"""')
        included.append(tree.body[0])  # docstring düğümü her zaman ilk sırada
    header_parts.extend(ast.unparse(n) for n in imports)
    included.extend(imports)

    # Sabitler olmadan "varsayılan zaman aşımı kaç" gibi sorular cevapsız
    # kalıyor: DEFAULT_TIMEOUT = ... satırı hiçbir parçaya girmiyordu.
    constant_lines = []
    for index, node in enumerate(tree.body):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        doc = _attribute_doc(tree.body, index)
        constant_lines.append(_assignment_text(node, doc))
        included.append(node)
        if doc:
            included.append(tree.body[index + 1])
    header_parts.extend(constant_lines)
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
    """Tek dosyayı indeksler. Yol, repo köküne göre göreli tutulur.

    Python `ast` ile ayrıştırılıyor — standart kütüphanede var ve tree-sitter'dan
    daha isabetli sonuç veriyor. Diğer diller tree-sitter grameriyle.
    """
    rel_path = path.resolve().relative_to(root.resolve()).as_posix()
    source = path.read_bytes().decode("utf-8", errors="replace")
    if path.suffix == ".py":
        return extract_from_source(source, rel_path)
    spec = spec_for(path.name)
    if spec is None:
        return []
    return extract_with_grammar(source, rel_path, spec)


def index_repo(
    root: Path, excludes: frozenset[str] = DEFAULT_EXCLUDES
) -> tuple[list[Chunk], IndexStats]:
    """Bir repoyu baştan sona indeksler; parçaları ve koşu özetini döner."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Dizin bulunamadı: {root}")

    chunks: list[Chunk] = []
    stats = IndexStats()
    for file_path in iter_source_files(root, excludes):
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
