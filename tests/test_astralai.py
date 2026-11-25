import datetime as dt
from produtos_core.teste import entrar
import os

os.environ["ASTRALAI_SEM_APP"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import astro  # noqa: E402
from app.config import Config  # noqa: E402
from app.main import criar_app  # noqa: E402

VISTOS = []


def llm_falso(msgs):
    VISTOS.append(msgs)
    return "Leitura simbólica: a carta pede coragem e calma."


def geo_falso(cidade):
    return {"cidade": "Goiânia", "estado": "Goiás", "pais": "BR", "lat": -16.68, "lon": -49.25, "fuso": "America/Sao_Paulo"}


@pytest.fixture
def amb(tmp_path):
    cfg = Config(dados=str(tmp_path), segredo_sessao="s", dev=True, cakto_segredo="ck", planos_por_produto={"p-plus": "plus"})
    app = criar_app(cfg, llm=llm_falso, geocod=geo_falso)
    c = TestClient(app)
    entrar(c, "ana@x.com")
    return c, app


def test_numerologia_pitagorica():
    n = astro.numerologia("Maria Silva", dt.date(1990, 3, 15))
    assert n["destino"] == astro.reduzir(1 + 5 + 0 + 3 + 1 + 9 + 9 + 0)
    assert 1 <= n["alma"] <= 33 and n["significados"]["destino"]
    with pytest.raises(ValueError):
        astro.numerologia("A", dt.date(1990, 1, 1))


def test_mapa_j2000_bate_com_referencia():
    m = astro.mapa(dt.datetime(2000, 1, 1, 12, 0, tzinfo=dt.timezone.utc), 51.48, 0.0)
    assert m["planetas"]["Sol"]["signo"] == "Capricórnio" and 10 <= m["planetas"]["Sol"]["grau"] <= 11
    assert m["planetas"]["Lua"]["signo"] == "Escorpião"
    assert m["ascendente"]["signo"] == "Áries"
    assert m["planetas"]["Sol"]["casa"] == 10  # Capricórnio é a 10ª a partir de Áries


def test_tarot_78_cartas_sem_repetir():
    assert len(astro.BARALHO) == 78
    c = astro.tirar(5)
    assert len({x["carta"] for x in c}) == 5 and [x["posicao"] for x in c][0] == "situação"
    assert astro.tirar(1, "u1:2026-11-16") == astro.tirar(1, "u1:2026-11-16")


def test_numerologia_publica_sem_login(amb):
    c, _ = amb
    r = TestClient(c.app).post("/api/numerologia", json={"nome": "João Pereira", "nascimento": "1985-07-20"})
    assert r.status_code == 200 and "destino" in r.json()


def test_perfil_mapa_e_gratis_x_plus(amb):
    c, _ = amb
    p = c.post("/api/perfis", json={"nome": "Ana Souza", "nascimento": "1992-08-09", "hora": "14:30", "cidade": "Goiânia, GO"}).json()
    assert p["principal"] and p["mapa"]["ascendente"]["signo"] in astro.SIGNOS and p["cidade"].startswith("Goiânia")
    # grátis: carta do dia (uma por dia, a mesma) e conversa
    d1 = c.post("/api/carta-do-dia").json()
    d2 = c.post("/api/carta-do-dia").json()
    assert d1["id"] == d2["id"] and len(d1["dados"]["cartas"]) == 1
    assert c.post("/api/conversa", json={"texto": "o que meu número de destino diz?"}).json()["resposta"]
    assert "Numerologia: destino" in VISTOS[-1][0]["content"]  # a IA recebe os números calculados
    # Plus libera tarô, mapa e sinastria
    assert c.post("/api/tarot", json={"pergunta": "amor"}).status_code == 402
    assert c.post("/api/cakto", json={"secret": "ck", "event": "purchase_approved", "data": {"id": "1", "customer": {"email": "ana@x.com"}, "product": {"id": "p-plus"}}}).json()["liberado"]
    t = c.post("/api/tarot", json={"pergunta": "amor", "cartas": 3}).json()
    assert len(t["dados"]["cartas"]) == 3
    assert c.post("/api/leitura/mapa").status_code == 200
    outro = c.post("/api/perfis", json={"nome": "Bruno Lima", "nascimento": "1990-01-02", "hora": "08:00", "cidade": "Goiânia"}).json()
    assert c.post(f"/api/leitura/sinastria/{outro['id']}").status_code == 200
    assert len(c.get("/api/leituras").json()["leituras"]) == 4


def test_limite_de_conversa_gratis(amb):
    c, _ = amb
    for i in range(12):
        assert c.post("/api/conversa", json={"texto": f"pergunta {i}"}).status_code == 200
    assert c.post("/api/conversa", json={"texto": "mais uma"}).status_code == 402


def test_memoria_e_skill_editavel(amb, monkeypatch):
    c, app = amb
    for i in range(4):
        c.post("/api/conversa", json={"texto": f"meu namorado João sumiu {i}"})
    sistema = VISTOS[-2][0]["content"]
    assert "SKILL astral-ai" in sistema and "baralho cigano" in sistema.lower() and "CÉU DE HOJE" in sistema
    assert "MEMÓRIA ATUAL" in VISTOS[-1][1]["content"]  # 4ª mensagem dispara a memória
    c.post("/api/conversa", json={"texto": "e agora?"})
    assert "Leitura simbólica" in VISTOS[-1][0]["content"]  # memória salva entra no contexto


def test_admin_login_e_skill(tmp_path):
    from app import admin
    cfg = Config(dados=str(tmp_path), segredo_sessao="s", dev=True)
    vistos = []
    app = criar_app(cfg, llm=lambda m: (vistos.append(m), "ok")[1], geocod=geo_falso)
    app.router.routes[:] = [r for r in app.router.routes if not getattr(r, "path", "").startswith("/api/painel")]
    app.include_router(admin.rotas(app.state.db, "s", "SELECT email, criado, 0 ativo FROM usuarios", usuario="lucas", senha="segredo", seguro=False))
    a = TestClient(app)
    assert a.get("/api/painel/skills").status_code == 401
    assert a.post("/api/painel/entrar", json={"usuario": "lucas", "senha": "errada"}).status_code == 401
    assert a.post("/api/painel/entrar", json={"usuario": "lucas", "senha": "segredo"}).status_code == 200
    assert [s["nome"] for s in a.get("/api/painel/skills").json()["skills"]] == ["astral-ai"]
    a.put("/api/painel/skills/astral-ai", json={"conteudo": "SKILL NOVA DO LUCAS"})
    s = a.get("/api/painel/skills/astral-ai").json()
    assert s["conteudo"] == "SKILL NOVA DO LUCAS" and len(s["historico"]) == 1 and s["por"] == "painel"
    entrar(a, "z@x.com")
    a.post("/api/conversa", json={"texto": "oi"})
    assert "SKILL NOVA DO LUCAS" in vistos[-1][0]["content"]
    assert a.post("/api/painel/skills/astral-ai/restaurar").json()["ok"] and "baralho cigano" in a.get("/api/painel/skills/astral-ai").json()["conteudo"].lower()
    assert a.get("/api/painel/resumo").json()["total"] == 1
