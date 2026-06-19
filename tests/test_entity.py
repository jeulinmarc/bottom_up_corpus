from __future__ import annotations

from bottom_up_corpus.entity import Entity, EntityRegistry


def _seeded(config) -> EntityRegistry:
    reg = EntityRegistry(config)
    reg.save([Entity(entity_id="alphabet", name="Alphabet / Google",
                     ciks=["1652044", "1288776"], note="2015 restructure")])
    return EntityRegistry(config).load()


def test_entity_normalizes_ciks():
    ent = Entity(entity_id="x", ciks=["1652044", 1288776])
    assert ent.ciks == ["0001652044", "0001288776"]
    assert ent.primary_cik == "0001652044"


def test_resolve_and_entity_id(config):
    reg = _seeded(config)
    ent = reg.resolve("1288776")
    assert ent is not None and ent.entity_id == "alphabet"
    assert reg.entity_id_for("1652044") == "alphabet"
    assert reg.entity_id_for("320193") == ""  # standalone


def test_expand_includes_siblings(config):
    reg = _seeded(config)
    # Querying the successor CIK expands to the predecessor too.
    assert set(reg.expand("1652044")) == {"0001652044", "0001288776"}
    # Standalone CIK expands to just itself.
    assert reg.expand("320193") == ["0000320193"]


def test_expand_all_dedups(config):
    reg = _seeded(config)
    out = reg.expand_all(["1652044", "320193", "1288776"])
    assert out.count("0001652044") == 1
    assert out.count("0001288776") == 1
    assert "0000320193" in out


def test_entities_iteration_is_deduped(config):
    reg = _seeded(config)
    ents = list(reg.entities())
    assert len(ents) == 1
    assert ents[0].entity_id == "alphabet"


def test_missing_registry_is_empty(config):
    reg = EntityRegistry(config).load()  # no file written
    assert reg.entity_id_for("320193") == ""
    assert reg.expand("320193") == ["0000320193"]
