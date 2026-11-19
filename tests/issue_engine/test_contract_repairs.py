"""Cross-project repair families: hypotheses, normal behavior, and safety boundaries."""
from axiomforge.issue_engine.contract_repairs import contract_candidates


def test_empty_optional_collection_preserves_no_restriction_vs_empty():
    code='def eligible(self, method):\n    if self.allowed_methods and method not in self.allowed_methods:\n        return False\n    return True\n'
    candidates=list(contract_candidates(code))
    variants=[x for x in candidates if x.kind=='optional-container-nonempty-distinction']
    assert variants
    namespace={}
    exec(variants[0].source,namespace)
    cls=type('Owner',(),{})
    o=cls();o.allowed_methods=set()
    assert namespace['eligible'](o,'GET') is False
    o.allowed_methods=None
    assert namespace['eligible'](o,'GET') is True
    o.allowed_methods={'POST'}
    assert namespace['eligible'](o,'GET') is False


def test_buffer_invariant_must_not_change_positive_values():
    src='def build(blocksize=16384):\n    return blocksize\n'
    m=next(x for x in contract_candidates(src) if x.kind=='positive-buffer-contract')
    ns={};exec(m.source,ns)
    assert ns['build'](123)==123
    for bad in (0,-1):
        try:ns['build'](bad)
        except ValueError:pass
        else:raise AssertionError('invalid I/O sizes must not silently pass')


def test_missing_exception_adapter_mapping_is_derived_from_peers():
    source=('class D:\n    class DataError(Exception):pass\n    class IntegrityError(Exception):pass\n'
            '    def get(self):\n        return {\n            asyncpg.exceptions.IntegrityError: self.IntegrityError,\n'
            '            asyncpg.exceptions.PostgresError: self.IntegrityError,\n'
            '            asyncpg.exceptions.OtherError: self.IntegrityError,\n        }\n')
    ms=list(contract_candidates(source))
    assert any('asyncpg.exceptions.DataError: self.DataError' in x.source for x in ms if x.kind=='peer-exception-map-completion')


def test_candidates_do_not_contain_issue_or_repository_names():
    import inspect
    from axiomforge.issue_engine import contract_repairs
    src=inspect.getsource(contract_repairs)
    for forbidden in ('sqlalchemy','sqlfluff','httpcore','urllib3','/issues/','13636','5044','5248'):
        assert forbidden not in src.lower()


def test_contract_repairs_ast_stays_parseable():
    import ast
    snippets=('def f(v):\n    if v and 4:return 1\n',
              'class C:\n    def x(self, chunk_size=4096):\n        pass\n')
    for source in snippets:
        for item in contract_candidates(source):
            ast.parse(item.source)
