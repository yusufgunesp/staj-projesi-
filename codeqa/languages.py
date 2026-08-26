"""Çok dilli sembol çıkarma: tree-sitter tabanlı ayrıştırıcılar.

Python için standart kütüphanedeki `ast` yeterli ve daha isabetli, o yüzden
`indexer.py` içinde kaldı. Diğer diller tree-sitter grameriyle ayrıştırılıyor.

Neden tree-sitter: her dil için ayrı bir ayrıştırıcı yazmak ya da düzenli
ifadeyle idare etmek iki türlü de kırılgan. Tree-sitter gramerleri hazır,
hatalı koda dayanıklı (yarım yazılmış dosyada bile ağaç üretiyor) ve düğüm
satır aralıklarını veriyor — `dosya:satır` referansı verebilmenin ön şartı bu.

Diller arasında düğüm adları farklı ama yapı aynı: fonksiyonlar, kapsayıcılar
(sınıf/struct/arayüz) ve dosya başlığı (import + sabitler). `LanguageSpec` bu
farkları tek yerde topluyor, çıkarma mantığı ortak.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache

from .models import Chunk

#: Bir kapsayıcının (sınıf, struct) özet metnine alınacak azami üye sayısı.
MAX_MEMBERS = 40

#: Modül başlığına alınacak azami satır. Uzun include blokları parçayı şişiriyor.
MAX_HEADER_LINES = 60


@dataclass(frozen=True)
class LanguageSpec:
    """Bir dilin tree-sitter düğüm adlarıyla ortak kavramlar arasındaki eşlemesi."""

    name: str
    extensions: tuple[str, ...]
    loader: Callable[[], object]
    #: Fonksiyon/metot tanımı olan düğümler.
    function_nodes: frozenset[str]
    #: Sınıf, struct, arayüz gibi üye barındıran düğümler.
    container_nodes: frozenset[str] = field(default_factory=frozenset)
    #: import / include / using satırları.
    import_nodes: frozenset[str] = field(default_factory=frozenset)
    #: Dosya seviyesi sabit tanımları (makro, const).
    constant_nodes: frozenset[str] = field(default_factory=frozenset)


def _load(module_name: str, attribute: str = "language"):
    """Grameri tembel yükler: kullanılmayan dil için modül import edilmiyor."""

    def loader():
        import importlib

        module = importlib.import_module(module_name)
        return getattr(module, attribute)()

    return loader


LANGUAGES: dict[str, LanguageSpec] = {
    "c": LanguageSpec(
        name="c",
        extensions=(".c", ".h"),
        loader=_load("tree_sitter_c"),
        function_nodes=frozenset({"function_definition"}),
        container_nodes=frozenset({"struct_specifier", "union_specifier", "enum_specifier"}),
        import_nodes=frozenset({"preproc_include"}),
        constant_nodes=frozenset({"preproc_def", "type_definition"}),
    ),
    "cpp": LanguageSpec(
        name="cpp",
        extensions=(".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
        loader=_load("tree_sitter_cpp"),
        function_nodes=frozenset({"function_definition"}),
        container_nodes=frozenset(
            {"class_specifier", "struct_specifier", "union_specifier", "enum_specifier"}
        ),
        import_nodes=frozenset({"preproc_include", "using_declaration"}),
        constant_nodes=frozenset({"preproc_def", "type_definition", "alias_declaration"}),
    ),
    "java": LanguageSpec(
        name="java",
        extensions=(".java",),
        loader=_load("tree_sitter_java"),
        function_nodes=frozenset({"method_declaration", "constructor_declaration"}),
        container_nodes=frozenset(
            {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
        ),
        import_nodes=frozenset({"import_declaration", "package_declaration"}),
        constant_nodes=frozenset(),
    ),
    "csharp": LanguageSpec(
        name="csharp",
        extensions=(".cs",),
        loader=_load("tree_sitter_c_sharp"),
        function_nodes=frozenset(
            {"method_declaration", "constructor_declaration", "property_declaration"}
        ),
        container_nodes=frozenset(
            {
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "record_declaration",
            }
        ),
        import_nodes=frozenset({"using_directive"}),
        constant_nodes=frozenset(),
    ),
    "go": LanguageSpec(
        name="go",
        extensions=(".go",),
        loader=_load("tree_sitter_go"),
        function_nodes=frozenset({"function_declaration", "method_declaration"}),
        container_nodes=frozenset({"type_declaration"}),
        import_nodes=frozenset({"import_declaration", "package_clause"}),
        constant_nodes=frozenset({"const_declaration", "var_declaration"}),
    ),
    "typescript": LanguageSpec(
        name="typescript",
        extensions=(".ts", ".tsx"),
        loader=_load("tree_sitter_typescript", "language_typescript"),
        function_nodes=frozenset(
            {"function_declaration", "method_definition", "function_signature"}
        ),
        container_nodes=frozenset(
            {"class_declaration", "interface_declaration", "enum_declaration"}
        ),
        import_nodes=frozenset({"import_statement"}),
        constant_nodes=frozenset({"type_alias_declaration"}),
    ),
    "javascript": LanguageSpec(
        name="javascript",
        extensions=(".js", ".jsx", ".mjs"),
        loader=_load("tree_sitter_javascript"),
        function_nodes=frozenset({"function_declaration", "method_definition"}),
        container_nodes=frozenset({"class_declaration"}),
        import_nodes=frozenset({"import_statement"}),
        constant_nodes=frozenset(),
    ),
}

#: Uzantıdan dile eşleme. Python burada yok: `indexer.py` onu `ast` ile işliyor.
EXTENSION_MAP: dict[str, LanguageSpec] = {
    extension: spec for spec in LANGUAGES.values() for extension in spec.extensions
}


def spec_for(path: str) -> LanguageSpec | None:
    """Dosya yolundan dil belirler; desteklenmeyen uzantıda None."""
    for extension, spec in EXTENSION_MAP.items():
        if path.endswith(extension):
            return spec
    return None


@cache
def _parser(language_name: str):
    """Dil başına tek ayrıştırıcı. Gramer yüklemek pahalı, tekrarlanmamalı."""
    from tree_sitter import Language, Parser

    spec = LANGUAGES[language_name]
    return Parser(Language(spec.loader()))


#: Ad taşıyan düğüm türleri. Diller arasında değişiyor, hepsi tek kümede.
_NAME_NODES = frozenset(
    {
        "identifier",
        "type_identifier",
        "field_identifier",
        "property_identifier",
        "qualified_identifier",
        "destructor_name",
        "operator_name",
    }
)


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _name_of(node, source: bytes) -> str:
    """Düğümün adını çıkarır.

    Diller adı farklı yerde tutuyor: çoğu `name` alanında, C/C++ ise
    `declarator` zincirinin ucunda (`int *foo(void)` → declarator içinde),
    Go ise `type_spec` alt düğümünde.
    """
    named = node.child_by_field_name("name")
    if named is not None:
        return _text(named, source)

    declarator = node.child_by_field_name("declarator")
    while declarator is not None:
        if declarator.type in _NAME_NODES:
            return _text(declarator, source)
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            break
        declarator = inner

    for child in node.named_children:
        if child.type == "type_spec":  # Go: type X struct {...}
            spec_name = child.child_by_field_name("name")
            if spec_name is not None:
                return _text(spec_name, source)
        if child.type in _NAME_NODES:
            return _text(child, source)
    return "<isimsiz>"


def _receiver_type(node, source: bytes) -> str | None:
    """Go metodunun alıcı tipi: `func (s *OrderService) Create(...)` → OrderService.

    Alıcı okunmazsa metot dosya seviyesi fonksiyon gibi görünüyor ve
    `OrderService.Create` yerine sadece `Create` diye indeksleniyor.
    """
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return None
    for child in _walk(receiver):
        if child.type == "type_identifier":
            return _text(child, source)
    return None


def _typedef_name(node, source: bytes) -> str | None:
    """İsimsiz struct'ın adını saran typedef'ten alır.

    C'de `typedef struct { ... } OrderService;` yaygın: struct'ın kendisi
    isimsiz, ad typedef'te. Ad çıkarılmazsa parça `<isimsiz>` olarak
    indeksleniyor ve aranamıyor.
    """
    parent = node.parent
    if parent is None or parent.type != "type_definition":
        return None
    declarator = parent.child_by_field_name("declarator")
    if declarator is not None and declarator.type in _NAME_NODES:
        return _text(declarator, source)
    return None


def _signature(node, source: bytes) -> str:
    """Gövdesiz imza: düğümün başından gövdenin başlangıcına kadar."""
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    text = source[node.start_byte : end].decode("utf-8", errors="replace")
    return " ".join(text.split())[:300]


def _container_summary(node, source: bytes, spec: LanguageSpec) -> str:
    """Kapsayıcının özeti: başlık + üye imzaları.

    Üye gövdeleri alınmıyor; onlar zaten ayrı parça olarak çıkıyor ve tekrar
    indekslemek hem maliyet hem arama kalitesi açısından zarar veriyor.
    """
    parts = [_signature(node, source)]
    members = [
        f"    {_signature(child, source)}"
        for child in _walk(node)
        if child is not node and child.type in spec.function_nodes
    ]
    if members:
        parts.append("    // üyeler:")
        parts.extend(members[:MAX_MEMBERS])
        if len(members) > MAX_MEMBERS:
            parts.append(f"    // ... ve {len(members) - MAX_MEMBERS} üye daha")
    return "\n".join(parts)


def _walk(node):
    """Düğümü ve tüm alt düğümlerini dolaşır."""
    yield node
    for child in node.named_children:
        yield from _walk(child)


def extract_from_source(source_text: str, rel_path: str, spec: LanguageSpec) -> list[Chunk]:
    """Tek bir kaynak dosyadan parçaları çıkarır."""
    source = source_text.encode("utf-8")
    tree = _parser(spec.name).parse(source)
    root = tree.root_node
    chunks: list[Chunk] = []

    def context(name: str, kind: str) -> str:
        return f"{rel_path} > {name} ({kind}) [{spec.name}]"

    def add(node, name: str, kind: str, text: str, parent: str | None) -> None:
        chunks.append(
            Chunk(
                source="code",
                kind=kind,
                path=rel_path,
                name=name,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                text=text,
                context=context(name, kind),
                signature=_signature(node, source) or None,
                parent=parent,
                language=spec.name,
            )
        )

    # 1) Dosya başlığı: include/import satırları ve dosya seviyesi sabitler.
    header_nodes = [
        child
        for child in root.named_children
        if child.type in spec.import_nodes or child.type in spec.constant_nodes
    ]
    if header_nodes:
        lines = [_text(node, source).strip() for node in header_nodes][:MAX_HEADER_LINES]
        module_name = rel_path.rsplit("/", 1)[-1]
        chunks.append(
            Chunk(
                source="code",
                kind="module",
                path=rel_path,
                name=module_name,
                start_line=header_nodes[0].start_point[0] + 1,
                end_line=header_nodes[-1].end_point[0] + 1,
                text="\n".join(lines),
                context=context(module_name, "module"),
                language=spec.name,
            )
        )

    # 2) Fonksiyonlar ve kapsayıcılar. Kapsayıcı içindeki fonksiyonlar "method",
    #    dışındakiler "function". İç içe fonksiyonlar ayrı parça değil — dış
    #    fonksiyonun gövdesinde zaten yer alıyorlar.
    def visit(node, parent: str | None, inside_function: bool) -> None:
        for child in node.named_children:
            if child.type in spec.function_nodes:
                if inside_function:
                    continue
                name = _name_of(child, source)
                # Go'da metot kapsayıcının içinde değil, alıcı tipiyle bağlı.
                owner = parent or _receiver_type(child, source)
                qualified = f"{owner}.{name}" if owner else name
                add(
                    child,
                    qualified,
                    "method" if owner else "function",
                    _text(child, source),
                    owner,
                )
                visit(child, parent, inside_function=True)
            elif child.type in spec.container_nodes:
                name = _name_of(child, source)
                if name == "<isimsiz>":
                    name = _typedef_name(child, source) or name
                qualified = f"{parent}.{name}" if parent else name
                add(child, qualified, "class", _container_summary(child, source, spec), parent)
                visit(child, qualified, inside_function=False)
            else:
                visit(child, parent, inside_function)

    visit(root, None, False)
    return chunks
