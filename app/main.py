"""Astral AI: guia espiritual por chat (tarô, baralho cigano, numerologia, astrologia e esoterismo) pela IA (Hermes).

A guia lembra de tudo: cada pessoa tem uma MEMÓRIA (resumo atualizado pela IA a cada 4 mensagens) que vai no contexto
junto com as últimas leituras, o céu do dia (calculado) e os dados de nascimento. A skill (skills/astral-ai.md) mora no
banco e é editável no painel /admin (app/admin.py) sem deploy.

Grátis: numerologia completa, carta do dia e um pouco de conversa por dia. Plus: conversa à vontade, tiragens de tarô,
mapa astral completo e sinastria (compatibilidade). Os cálculos são feitos aqui (app/astro.py); a IA só interpreta.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import admin, astro, auth, mailer, senha_rotas
from app.config import Config, carregar
from app.db import Banco, agora

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("astralai")
COOKIE = "astralai_s"
ESTATICO = Path(__file__).resolve().parent.parent / "static"
GRATIS_MSGS_DIA = 12

PLANOS = {
    "plus": {"nome": "Plus", "preco": 29.90, "depois": 39.90,
             "itens": ["Conversa sem limite com a sua guia, que lembra de tudo", "Tarô de 3 e de 5 cartas", "Mapa astral completo", "Sinastria (compatibilidade)",
                       "Leituras guardadas no histórico"]},
    "premium": {"nome": "Premium", "preco": 59.90, "depois": 79.90,
                "itens": ["Tudo do Plus", "Mapas e sinastrias para a família toda", "Previsão do mês pelo seu mapa",
                          "Ritual de limpeza energética guiado", "Atendimento prioritário"]},
}

CABECA = """Você é a Astral AI, guia espiritual pessoal da Rangel Tech. Siga a skill astral-ai abaixo em tudo.
=== SKILL astral-ai ===
{skill}
=== FIM DA SKILL ===
CONTEXTO DA PESSOA:
{contexto}"""
RESUMIR = """Você mantém a memória de uma guia espiritual sobre a pessoa que conversa com ela.
Reescreva a MEMÓRIA incluindo o que importa para as próximas conversas: nomes (parceiro, família, chefe, amigos),
situações em andamento (amor, trabalho, dinheiro, família, emoções), sonhos contados, medos, metas, perguntas que se repetem,
leituras feitas e conselhos dados, datas citadas. Tópicos curtos, no máximo 30 linhas. Responda só com a memória."""
MEMORIA_A_CADA = 4


def criar_app(cfg: Config | None = None, llm=None, geocod=None) -> FastAPI:
    cfg = cfg or carregar()
    os.makedirs(cfg.dados, exist_ok=True)
    db = Banco(os.path.join(cfg.dados, "astralai.db"))
    admin.preparar(db)
    db.con.executescript("CREATE TABLE IF NOT EXISTS memorias (usuario INTEGER PRIMARY KEY, texto TEXT NOT NULL, atualizado REAL NOT NULL);")
    app = FastAPI(title="Astral AI", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg, app.state.db = cfg, db

    def chamar_llm(mensagens: list[dict], max_tokens: int = 900) -> str:
        if llm:
            return llm(mensagens)
        r = httpx.post(cfg.llm_base + "/chat/completions", headers={"Authorization": f"Bearer {cfg.llm_key}"}, timeout=180,
                       json={"model": cfg.llm_modelo, "messages": mensagens, "temperature": 0.8, "max_tokens": max_tokens})
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()

    if cfg.demo_email:
        u = auth.usuario_por_email(db, cfg.demo_email.lower())
        db.exec("INSERT INTO assinaturas(usuario,plano,ativo_ate) VALUES(?,?,?) ON CONFLICT(usuario) DO UPDATE SET plano=excluded.plano, ativo_ate=excluded.ativo_ate",
                (u["id"], "premium", agora() + 3650 * 86400))

    # ------------------------------------------------ ajudantes
    def usuario(request: Request, obrigatorio: bool = True) -> dict | None:
        u = auth.usuario_da_sessao(db, cfg.segredo_sessao, request.cookies.get(COOKIE))
        if not u and obrigatorio:
            raise HTTPException(401, "entre na sua conta")
        return u

    def plano_de(u: dict) -> str | None:
        a = db.um("SELECT * FROM assinaturas WHERE usuario=?", (u["id"],))
        return a["plano"] if a and a["ativo_ate"] > agora() else None

    def exigir_plano(u: dict, oque: str):
        if not plano_de(u):
            raise HTTPException(402, f"{oque} faz parte do plano Plus; a numerologia e a carta do dia continuam grátis")

    def hoje_ini() -> float:
        h = dt.datetime.now(dt.timezone(dt.timedelta(hours=-3)))
        return h.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def perfil_principal(u: dict) -> dict | None:
        return db.um("SELECT * FROM perfis WHERE usuario=? AND principal=1", (u["id"],))

    def vista_perfil(p: dict) -> dict:
        return {"id": p["id"], "principal": bool(p["principal"]), "nome": p["nome"], "nascimento": p["nascimento"], "hora": p["hora"],
                "cidade": p["cidade"], "numerologia": json.loads(p["numerologia"] or "null"), "mapa": json.loads(p["mapa"] or "null")}

    def calcular(nome: str, nasc: str, hora: str, cidade: str) -> dict:
        try:
            data = dt.date.fromisoformat(nasc)
        except ValueError:
            raise HTTPException(422, "data de nascimento inválida")
        if not dt.date(1900, 1, 1) <= data <= dt.date.today():
            raise HTTPException(422, "data de nascimento fora do intervalo")
        try:
            num = astro.numerologia(nome, data)
        except ValueError as e:
            raise HTTPException(422, str(e))
        out = {"numerologia": num, "mapa": None, "lat": None, "lon": None, "fuso": None, "cidade": cidade}
        if hora and cidade:
            try:
                h = dt.time.fromisoformat(hora)
            except ValueError:
                raise HTTPException(422, "hora inválida (use 14:30)")
            try:
                g = (geocod or astro.geocodificar)(cidade)
            except ValueError as e:
                raise HTTPException(422, str(e))
            except Exception:  # noqa: BLE001
                raise HTTPException(502, "não consegui localizar a cidade agora; tente de novo")
            out.update(lat=g["lat"], lon=g["lon"], fuso=g["fuso"], cidade=f"{g['cidade']}, {g['estado']}".strip(", "))
            out["mapa"] = astro.mapa(astro.para_utc(data, h, g["fuso"]), g["lat"], g["lon"])
        return out

    def resumo_mapa(m: dict | None) -> str:
        if not m:
            return "sem mapa (falta hora e cidade de nascimento)"
        pl = ", ".join(f"{n} em {p['signo']} {p['grau']}° (casa {p['casa']})" for n, p in m["planetas"].items())
        return f"Ascendente {m['ascendente']['signo']} {m['ascendente']['grau']}°, Meio do Céu {m['meio_do_ceu']['signo']}; {pl}"

    def contexto(u: dict) -> str:
        p = perfil_principal(u)
        if not p:
            return "A pessoa ainda não informou os dados de nascimento."
        v = vista_perfil(p)
        n = v["numerologia"] or {}
        return (f"Pessoa: {v['nome']}, nascida em {v['nascimento']}{' às ' + v['hora'] if v['hora'] else ''}{' em ' + v['cidade'] if v['cidade'] else ''}.\n"
                f"Numerologia: destino {n.get('destino')}, expressão {n.get('expressao')}, alma {n.get('alma')}, personalidade {n.get('personalidade')}, ano pessoal {n.get('ano_pessoal')}.\n"
                f"Mapa: {resumo_mapa(v['mapa'])}.\nPlano: {plano_de(u) or 'grátis'}.")

    CEU: dict = {}

    def ceu_de_hoje() -> str:
        dia = dt.date.today().isoformat()
        if dia not in CEU:
            try:
                m = astro.mapa(dt.datetime.now(dt.timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0), -15.8, -47.9)
                CEU.clear()
                CEU[dia] = "; ".join(f"{n} em {p['signo']} {p['grau']}°" for n, p in m["planetas"].items())
            except Exception:  # noqa: BLE001
                return "indisponível"
        return CEU[dia]

    def sistema(u: dict) -> str:
        mem = db.um("SELECT texto FROM memorias WHERE usuario=?", (u["id"],))
        lei = db.todos("SELECT tipo, texto, criado FROM leituras WHERE usuario=? ORDER BY criado DESC LIMIT 4", (u["id"],))
        ctx = (contexto(u) + f"\nHoje: {dt.date.today().strftime('%d/%m/%Y')}. CÉU DE HOJE: {ceu_de_hoje()}."
               + f"\nMEMÓRIA (o que você já sabe desta pessoa):\n{mem['texto'] if mem else 'Ainda nada: é o começo da relação.'}"
               + ("\nLEITURAS RECENTES:\n" + "\n".join(f"- {l['tipo']} em {dt.date.fromtimestamp(l['criado']).strftime('%d/%m')}: {l['texto'][:400]}" for l in lei) if lei else ""))
        return CABECA.format(skill=admin.skill(db, "astral-ai"), contexto=ctx)

    def atualizar_memoria(uid: int) -> None:
        try:
            mem = db.um("SELECT texto FROM memorias WHERE usuario=?", (uid,))
            hist = db.todos("SELECT papel, texto FROM mensagens WHERE usuario=? ORDER BY criado DESC, id DESC LIMIT 24", (uid,))[::-1]
            conversa = "\n".join(f"{'Pessoa' if m['papel'] == 'usuario' else 'Guia'}: {m['texto'][:600]}" for m in hist)
            novo = chamar_llm([{"role": "system", "content": RESUMIR},
                               {"role": "user", "content": f"MEMÓRIA ATUAL:\n{mem['texto'] if mem else '(vazia)'}\n\nCONVERSA RECENTE:\n{conversa}"}], 900)
            if novo.strip():
                db.exec("INSERT INTO memorias(usuario,texto,atualizado) VALUES(?,?,?) ON CONFLICT(usuario) DO UPDATE SET texto=excluded.texto, atualizado=excluded.atualizado",
                        (uid, novo.strip()[:8000], agora()))
        except Exception:  # noqa: BLE001
            log.exception("memória não atualizada")

    def guardar(u: dict, tipo: str, dados: dict, texto: str) -> dict:
        lid = db.exec("INSERT INTO leituras(usuario,tipo,dados,texto,criado) VALUES(?,?,?,?,?)",
                      (u["id"], tipo, json.dumps(dados, ensure_ascii=False), texto, agora())).lastrowid
        return {"id": lid, "tipo": tipo, "dados": dados, "texto": texto, "criado": agora()}

    @app.exception_handler(json.JSONDecodeError)
    async def corpo_invalido(request: Request, exc: Exception):
        return JSONResponse({"detail": "corpo inválido (envie JSON)"}, status_code=400)

    @app.get("/health")
    def health():
        return {"ok": True}

    # ------------------------------------------------ login (mesmo padrão dos outros produtos)
    def _login(u: dict) -> JSONResponse:
        token = auth.criar_sessao(db, cfg.segredo_sessao, u["id"])
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token, max_age=auth.SESSAO_S, httponly=True, secure=not cfg.dev, samesite="lax", path="/")
        return resp

    @app.post("/api/entrar/codigo")
    async def pedir_codigo(request: Request):
        b = await request.json()
        email = auth.normalizar_email(b.get("email"))
        if not email:
            raise HTTPException(422, "e-mail inválido")
        cod, motivo = auth.novo_codigo(db, cfg.segredo_sessao, email)
        if motivo == "espere":
            raise HTTPException(429, "acabamos de mandar um código; espere alguns segundos")
        out = {"ok": True, "enviado": mailer.codigo(cfg, email, cod)}
        if cfg.dev:
            out["codigo_dev"] = cod
        return out

    @app.post("/api/entrar")
    async def entrar(request: Request):
        b = await request.json()
        email = auth.normalizar_email(b.get("email"))
        if not email:
            raise HTTPException(422, "e-mail inválido")
        r = auth.conferir_codigo(db, cfg.segredo_sessao, email, str(b.get("codigo") or ""))
        if r != "ok":
            raise HTTPException(400, {"invalido": "código errado", "expirado": "código vencido; peça outro", "tentativas": "muitas tentativas; peça outro código"}[r])
        return _login(auth.usuario_por_email(db, email))

    senha_rotas.registrar(app, db, cfg, mailer, _login, usuario)

    @app.post("/api/sair")
    def sair(request: Request):
        auth.encerrar_sessao(db, cfg.segredo_sessao, request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ------------------------------------------------ público: planos e numerologia grátis (isca da página)
    @app.get("/api/planos")
    def planos():
        return {"planos": [{"codigo": k, **v, "checkout": cfg.checkouts.get(k) or "", "lancamento_ate": cfg.lancamento_ate} for k, v in PLANOS.items()]}

    @app.post("/api/numerologia")
    async def numerologia_publica(request: Request):
        b = await request.json()
        try:
            data = dt.date.fromisoformat(str(b.get("nascimento") or ""))
            return astro.numerologia(str(b.get("nome") or ""), data)
        except ValueError as e:
            raise HTTPException(422, str(e) if "nome" in str(e) else "data inválida")

    # ------------------------------------------------ conta e perfis natais
    @app.get("/api/conta")
    def conta(request: Request):
        u = usuario(request)
        usadas = db.um("SELECT COUNT(*) n FROM mensagens WHERE usuario=? AND papel='usuario' AND criado>=?", (u["id"], hoje_ini()))["n"]
        p = perfil_principal(u)
        return {"email": u["email"], "plano": plano_de(u), "msgs_hoje": usadas, "limite_gratis": GRATIS_MSGS_DIA,
                "tem_senha": bool(u.get("senha_hash")), "perfil": vista_perfil(p) if p else None, "planos": planos()["planos"]}

    @app.get("/api/perfis")
    def perfis(request: Request):
        u = usuario(request)
        return {"perfis": [vista_perfil(p) for p in db.todos("SELECT * FROM perfis WHERE usuario=? ORDER BY principal DESC, criado", (u["id"],))]}

    @app.post("/api/perfis")
    async def salvar_perfil(request: Request):
        u = usuario(request)
        b = await request.json()
        nome, nasc = str(b.get("nome") or "").strip()[:120], str(b.get("nascimento") or "")
        hora, cidade = str(b.get("hora") or "").strip()[:5], str(b.get("cidade") or "").strip()[:120]
        principal = 1 if b.get("principal") or not perfil_principal(u) else 0
        if not principal:
            exigir_plano(u, "Guardar o mapa de outras pessoas")
            if db.um("SELECT COUNT(*) n FROM perfis WHERE usuario=?", (u["id"],))["n"] >= (12 if plano_de(u) == "premium" else 3):
                raise HTTPException(402, "limite de pessoas do seu plano")
        c = calcular(nome, nasc, hora, cidade)
        if principal:
            db.exec("DELETE FROM perfis WHERE usuario=? AND principal=1", (u["id"],))
        pid = db.exec("INSERT INTO perfis(usuario,principal,nome,nascimento,hora,cidade,lat,lon,fuso,numerologia,mapa,criado) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (u["id"], principal, nome, nasc, hora, c["cidade"], c["lat"], c["lon"], c["fuso"], json.dumps(c["numerologia"], ensure_ascii=False),
                       json.dumps(c["mapa"], ensure_ascii=False) if c["mapa"] else None, agora())).lastrowid
        return vista_perfil(db.um("SELECT * FROM perfis WHERE id=?", (pid,)))

    @app.delete("/api/perfis/{pid}")
    def apagar_perfil(pid: int, request: Request):
        u = usuario(request)
        db.exec("DELETE FROM perfis WHERE id=? AND usuario=? AND principal=0", (pid, u["id"]))
        return {"ok": True}

    # ------------------------------------------------ leituras
    @app.get("/api/leituras")
    def leituras(request: Request):
        u = usuario(request)
        return {"leituras": [{**x, "dados": json.loads(x["dados"])} for x in db.todos("SELECT * FROM leituras WHERE usuario=? ORDER BY criado DESC LIMIT 60", (u["id"],))]}

    @app.post("/api/carta-do-dia")
    def carta_do_dia(request: Request):
        u = usuario(request)
        dia = dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).date().isoformat()
        ja = db.um("SELECT * FROM leituras WHERE usuario=? AND tipo='carta_do_dia' AND criado>=?", (u["id"], hoje_ini()))
        if ja:
            return {**ja, "dados": json.loads(ja["dados"])}
        cartas = astro.tirar(1, semente=f"{u['id']}:{dia}")
        texto = chamar_llm([{"role": "system", "content": sistema(u)},
                            {"role": "user", "content": "Interprete a minha carta do dia em até 6 linhas, com um conselho prático para hoje. Carta: "
                                                        + json.dumps(cartas, ensure_ascii=False)}], 500)
        return guardar(u, "carta_do_dia", {"cartas": cartas}, texto)

    @app.post("/api/tarot")
    async def tarot(request: Request):
        u = usuario(request)
        exigir_plano(u, "A tiragem de tarô")
        b = await request.json()
        n = 5 if int(b.get("cartas") or 3) == 5 else 3
        pergunta = str(b.get("pergunta") or "").strip()[:400] or "Como está o meu caminho agora?"
        cartas = astro.tirar(n)
        texto = chamar_llm([{"role": "system", "content": sistema(u)},
                            {"role": "user", "content": f"Faça a leitura de tarô para a pergunta: \"{pergunta}\". Cartas tiradas (posição, carta, invertida): "
                                                        f"{json.dumps(cartas, ensure_ascii=False)}. Leia carta por carta e feche com uma síntese e um conselho."}], 1200)
        return guardar(u, "tarot", {"pergunta": pergunta, "cartas": cartas}, texto)

    @app.post("/api/leitura/mapa")
    def leitura_mapa(request: Request):
        u = usuario(request)
        exigir_plano(u, "O mapa astral completo")
        p = perfil_principal(u)
        if not p or not p["mapa"]:
            raise HTTPException(422, "informe a hora e a cidade de nascimento para calcular o mapa")
        texto = chamar_llm([{"role": "system", "content": sistema(u)},
                            {"role": "user", "content": "Faça a leitura completa do meu mapa astral: Sol, Lua e Ascendente primeiro, depois os planetas "
                                                        "pessoais, o equilíbrio dos elementos e um fechamento sobre propósito. Use títulos curtos."}], 1800)
        return guardar(u, "mapa", {"mapa": json.loads(p["mapa"])}, texto)

    @app.post("/api/leitura/sinastria/{pid}")
    def sinastria(pid: int, request: Request):
        u = usuario(request)
        exigir_plano(u, "A sinastria")
        eu, outro = perfil_principal(u), db.um("SELECT * FROM perfis WHERE id=? AND usuario=?", (pid, u["id"]))
        if not eu or not outro:
            raise HTTPException(404, "perfil não encontrado")
        a, b2 = vista_perfil(eu), vista_perfil(outro)
        dados = {"pessoa1": {"nome": a["nome"], "numerologia": a["numerologia"], "mapa": resumo_mapa(a["mapa"])},
                 "pessoa2": {"nome": b2["nome"], "numerologia": b2["numerologia"], "mapa": resumo_mapa(b2["mapa"])}}
        texto = chamar_llm([{"role": "system", "content": sistema(u)},
                            {"role": "user", "content": "Faça a sinastria (compatibilidade) entre estas duas pessoas: pontos de encontro, atritos e como cuidar "
                                                        f"da relação. Dados calculados: {json.dumps(dados, ensure_ascii=False)}"}], 1500)
        return guardar(u, "sinastria", dados, texto)

    # ------------------------------------------------ conversa
    @app.get("/api/conversa")
    def conversa(request: Request):
        u = usuario(request)
        return {"mensagens": db.todos("SELECT papel, texto, criado FROM mensagens WHERE usuario=? ORDER BY criado DESC, id DESC LIMIT 60", (u["id"],))[::-1]}

    @app.post("/api/conversa")
    async def falar(request: Request):
        u = usuario(request)
        b = await request.json()
        texto = str(b.get("texto") or "").strip()[:1500]
        if not texto:
            raise HTTPException(422, "escreva a sua pergunta")
        if not plano_de(u):
            usadas = db.um("SELECT COUNT(*) n FROM mensagens WHERE usuario=? AND papel='usuario' AND criado>=?", (u["id"], hoje_ini()))["n"]
            if usadas >= GRATIS_MSGS_DIA:
                raise HTTPException(402, f"as {GRATIS_MSGS_DIA} mensagens grátis de hoje acabaram; com o Plus a conversa é sem limite")
        hist = db.todos("SELECT papel, texto FROM mensagens WHERE usuario=? ORDER BY criado DESC, id DESC LIMIT 16", (u["id"],))[::-1]
        msgs = [{"role": "system", "content": sistema(u)}]
        msgs += [{"role": "user" if m["papel"] == "usuario" else "assistant", "content": m["texto"]} for m in hist]
        msgs.append({"role": "user", "content": texto})
        db.exec("INSERT INTO mensagens(usuario,papel,texto,criado) VALUES(?,?,?,?)", (u["id"], "usuario", texto, agora()))
        try:
            resposta = chamar_llm(msgs)
        except Exception:  # noqa: BLE001
            raise HTTPException(502, "a IA não respondeu agora; tente de novo em instantes")
        db.exec("INSERT INTO mensagens(usuario,papel,texto,criado) VALUES(?,?,?,?)", (u["id"], "ia", resposta, agora()))
        n = db.um("SELECT COUNT(*) n FROM mensagens WHERE usuario=? AND papel='usuario'", (u["id"],))["n"]
        if n % MEMORIA_A_CADA == 0:
            (atualizar_memoria(u["id"]) if llm else threading.Thread(target=atualizar_memoria, args=(u["id"],), daemon=True).start())
        return {"resposta": resposta}

    # ------------------------------------------------ Cakto
    @app.post("/api/cakto")
    async def cakto(request: Request):
        cru = await request.body()
        try:
            b = json.loads(cru)
        except ValueError:
            raise HTTPException(400, "json")
        if not cfg.cakto_segredo or not hmac.compare_digest(str(b.get("secret") or ""), cfg.cakto_segredo):
            raise HTTPException(401, "segredo")
        evento = str(b.get("event") or "")
        dados = b.get("data") or {}
        if isinstance(dados, list):
            dados = dados[0] if dados else {}
        eid = str(dados.get("id") or hashlib.sha256(cru).hexdigest()[:24]) + ":" + evento
        if db.um("SELECT 1 FROM eventos_cakto WHERE id=?", (eid,)):
            return {"ok": True, "repetido": True}
        db.exec("INSERT INTO eventos_cakto(id,recebido,corpo) VALUES(?,?,?)", (eid, agora(), cru.decode("utf-8", "replace")[:20000]))
        email = auth.normalizar_email((dados.get("customer") or {}).get("email"))
        plano = cfg.planos_por_produto.get(str((dados.get("product") or {}).get("id") or ""))
        if not email or plano not in PLANOS:
            return {"ok": True, "ignorado": "sem email ou produto desconhecido"}
        u = auth.usuario_por_email(db, email)
        a = db.um("SELECT * FROM assinaturas WHERE usuario=?", (u["id"],))
        if evento in ("purchase_approved", "subscription_renewed", "subscription_created"):
            base = max(agora(), a["ativo_ate"] if a else 0)
            db.exec("INSERT INTO assinaturas(usuario,plano,ativo_ate,pedido) VALUES(?,?,?,?) ON CONFLICT(usuario) DO UPDATE SET plano=excluded.plano, ativo_ate=excluded.ativo_ate, pedido=excluded.pedido",
                    (u["id"], plano, base + 32 * 86400, str(dados.get("id") or "")))
            return {"ok": True, "liberado": email}
        if evento in ("refund", "chargeback", "subscription_canceled", "subscription_cancelled"):
            db.exec("UPDATE assinaturas SET ativo_ate=? WHERE usuario=?", (agora(), u["id"]))
            return {"ok": True, "revogado": email}
        return {"ok": True, "ignorado": evento}

    # ------------------------------------------------ páginas
    app.include_router(admin.rotas(db, cfg.segredo_sessao, """SELECT u.email, u.criado, a.plano, a.ativo_ate, (a.ativo_ate > strftime('%s','now')) ativo
        FROM usuarios u LEFT JOIN assinaturas a ON a.usuario=u.id ORDER BY u.criado DESC""", seguro=not cfg.dev))

    @app.get("/admin", response_class=HTMLResponse)
    def pagina_admin():
        return FileResponse(ESTATICO / "admin.html")

    app.mount("/s", StaticFiles(directory=ESTATICO), name="estatico")

    @app.get("/", response_class=HTMLResponse)
    def inicio():
        return FileResponse(ESTATICO / "index.html")

    @app.get("/painel", response_class=HTMLResponse)
    def painel():
        return FileResponse(ESTATICO / "painel.html")

    return app


app = criar_app() if os.environ.get("ASTRALAI_SEM_APP") != "1" else None
