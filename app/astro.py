"""Cálculos do Astral AI, sem IA: numerologia pitagórica, mapa astral e tarô. A IA (Hermes) só interpreta o que sai daqui.

Mapa astral: posições reais com skyfield (MIT) + efemérides DE421 da NASA/JPL (domínio público). Zodíaco tropical, eclíptica
verdadeira da data. Ascendente e Meio do Céu pela hora sideral local; casas por signo inteiro (a 1ª casa é o signo do Ascendente).
Sem bibliotecas AGPL (Kerykeion/Swiss Ephemeris ficaram de fora de propósito).
"""
from __future__ import annotations

import datetime as dt
import math
import random
import unicodedata
from functools import lru_cache
from pathlib import Path

SIGNOS = ["Áries", "Touro", "Gêmeos", "Câncer", "Leão", "Virgem", "Libra", "Escorpião", "Sagitário", "Capricórnio", "Aquário", "Peixes"]
ELEMENTO = {s: e for s, e in zip(SIGNOS, ["fogo", "terra", "ar", "água"] * 3)}

# ------------------------------------------------------------------ numerologia
_VALOR = {c: (i % 9) + 1 for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
_VOGAIS = set("AEIOUY")
SIGNIFICADO = {
    1: "Liderança e começo: veio para abrir caminhos e agir por conta própria.",
    2: "Parceria e sensibilidade: brilha unindo pessoas e cuidando dos detalhes.",
    3: "Expressão e criatividade: comunicação, alegria e talento para inspirar.",
    4: "Construção e firmeza: disciplina, método e segurança para quem está perto.",
    5: "Liberdade e mudança: movimento, curiosidade e coragem para o novo.",
    6: "Amor e responsabilidade: família, beleza e vontade de cuidar.",
    7: "Busca e sabedoria: intuição forte, estudo e mergulho no que é profundo.",
    8: "Realização e poder: ambição, prosperidade e capacidade de comandar.",
    9: "Compaixão e encerramento de ciclos: generosidade e visão do todo.",
    11: "Número mestre da intuição: inspiração, sensibilidade e propósito espiritual.",
    22: "Número mestre do construtor: transformar grandes sonhos em obra concreta.",
    33: "Número mestre do amor: servir, ensinar e curar pelo exemplo.",
}


def _limpo(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().upper()
    return "".join(c for c in t if c.isalpha())


def reduzir(n: int) -> int:
    while n > 9 and n not in (11, 22, 33):
        n = sum(int(d) for d in str(n))
    return n


def numerologia(nome: str, nascimento: dt.date) -> dict:
    letras = _limpo(nome)
    if len(letras) < 2:
        raise ValueError("informe o nome completo")
    destino = reduzir(sum(int(d) for d in nascimento.strftime("%d%m%Y")))
    expressao = reduzir(sum(_VALOR[c] for c in letras))
    alma = reduzir(sum(_VALOR[c] for c in letras if c in _VOGAIS))
    personalidade = reduzir(sum(_VALOR[c] for c in letras if c not in _VOGAIS))
    hoje = dt.date.today()
    ano_pessoal = reduzir(sum(int(d) for d in f"{nascimento:%d%m}{hoje.year}"))
    nums = {"destino": destino, "expressao": expressao, "alma": alma, "personalidade": personalidade, "ano_pessoal": ano_pessoal}
    return {**nums, "significados": {k: SIGNIFICADO.get(v, "") for k, v in nums.items()}}


ANIMAIS_CHINESES = ["Rato", "Boi", "Tigre", "Coelho", "Dragão", "Serpente", "Cavalo", "Cabra", "Macaco", "Galo", "Cão", "Porco"]
ELEMENTOS_CHINESES = ["Metal", "Metal", "Água", "Água", "Madeira", "Madeira", "Fogo", "Fogo", "Terra", "Terra"]


def signo_chines(ano: int, mes: int = 6, dia: int = 1) -> dict:
    """Animal e elemento do horóscopo chinês. Antes de ~4 de fevereiro conta o ano anterior (Ano Novo chinês varia
    de 21/01 a 20/02; nascidos nessa janela recebem o aviso)."""
    a = ano - 1 if (mes, dia) < (2, 4) else ano
    out = {"animal": ANIMAIS_CHINESES[(a - 2020) % 12], "elemento": ELEMENTOS_CHINESES[a % 10], "ano_chines": a}
    out["aviso"] = "nasceu perto do Ano Novo chinês: confirme a data exata do ano" if (1, 21) <= (mes, dia) <= (2, 20) else ""
    return out


# ------------------------------------------------------------------ mapa astral
EFEM = Path(__import__("os").environ.get("ASTRALAI_EFEM", Path(__file__).resolve().parent.parent / "efem"))
CORPOS = [("Sol", "sun"), ("Lua", "moon"), ("Mercúrio", "mercury"), ("Vênus", "venus"), ("Marte", "mars"),
          ("Júpiter", "jupiter barycenter"), ("Saturno", "saturn barycenter"), ("Urano", "uranus barycenter"),
          ("Netuno", "neptune barycenter"), ("Plutão", "pluto barycenter")]


@lru_cache(maxsize=1)
def _efem():
    from skyfield.api import Loader
    carregar = Loader(str(EFEM))
    return carregar.timescale(), carregar("de421.bsp")


def signo_de(lon: float) -> dict:
    lon = float(lon) % 360
    s = SIGNOS[int(lon // 30)]
    return {"signo": s, "grau": round(lon % 30, 1), "longitude": round(lon, 2), "elemento": ELEMENTO[s]}


def mapa(nascimento_utc: dt.datetime, lat: float, lon: float) -> dict:
    """nascimento_utc com tzinfo=UTC; lat/lon em graus (sul e oeste negativos)."""
    from skyfield.framelib import ecliptic_frame
    ts, eph = _efem()
    t = ts.from_datetime(nascimento_utc)
    terra = eph["earth"]
    planetas = {}
    for nome, alvo in CORPOS:
        lat_e, lon_e, _ = terra.at(t).observe(eph[alvo]).apparent().frame_latlon(ecliptic_frame)
        planetas[nome] = signo_de(lon_e.degrees)
    eps = math.radians(23.4392911 - 0.0130042 * ((t.tt - 2451545.0) / 36525))  # obliquidade média (suficiente para signo/grau)
    lst = math.radians((t.gast * 15 + lon) % 360)
    phi = math.radians(lat)
    asc = math.degrees(math.atan2(math.cos(lst), -(math.sin(lst) * math.cos(eps) + math.tan(phi) * math.sin(eps)))) % 360
    mc = math.degrees(math.atan2(math.sin(lst), math.cos(lst) * math.cos(eps))) % 360
    asc_i = int(asc // 30)
    casas = {n: ((int(p["longitude"] // 30) - asc_i) % 12) + 1 for n, p in planetas.items()}
    elementos = {}
    for p in planetas.values():
        elementos[p["elemento"]] = elementos.get(p["elemento"], 0) + 1
    return {"planetas": {n: {**p, "casa": casas[n]} for n, p in planetas.items()}, "ascendente": signo_de(asc),
            "meio_do_ceu": signo_de(mc), "elementos": elementos, "sistema_casas": "signo inteiro"}


def geocodificar(cidade: str, cliente=None) -> dict:
    """Cidade -> lat, lon e fuso (Open-Meteo, grátis, sem chave). Brasil primeiro."""
    import httpx
    c = cliente or httpx.Client(timeout=15)
    r = c.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": cidade.split(",")[0].strip(), "count": 5, "language": "pt"})
    r.raise_for_status()
    res = r.json().get("results") or []
    if not res:
        raise ValueError("não achei essa cidade; escreva como 'Goiânia, GO'")
    uf = cidade.split(",")[1].strip().lower() if "," in cidade else ""
    res.sort(key=lambda x: (x.get("country_code") != "BR", uf and uf not in (x.get("admin1") or "").lower()[:2] + (x.get("admin1") or "").lower()))
    x = res[0]
    return {"cidade": x["name"], "estado": x.get("admin1", ""), "pais": x.get("country_code", ""), "lat": x["latitude"], "lon": x["longitude"], "fuso": x.get("timezone", "America/Sao_Paulo")}


def para_utc(data: dt.date, hora: dt.time, fuso: str) -> dt.datetime:
    from zoneinfo import ZoneInfo
    return dt.datetime.combine(data, hora, tzinfo=ZoneInfo(fuso)).astimezone(dt.timezone.utc)


# ------------------------------------------------------------------ tarô (78 cartas)
MAIORES = [
    ("O Louco", "começo, salto de fé, liberdade"), ("O Mago", "iniciativa, habilidade, poder pessoal"),
    ("A Sacerdotisa", "intuição, segredo, sabedoria interior"), ("A Imperatriz", "abundância, fertilidade, cuidado"),
    ("O Imperador", "estrutura, autoridade, estabilidade"), ("O Hierofante", "tradição, ensinamento, fé"),
    ("Os Enamorados", "escolha, amor, união"), ("O Carro", "vitória, direção, força de vontade"),
    ("A Força", "coragem, paciência, domínio de si"), ("O Eremita", "introspecção, busca, prudência"),
    ("A Roda da Fortuna", "ciclos, sorte, virada"), ("A Justiça", "equilíbrio, verdade, consequência"),
    ("O Enforcado", "pausa, nova perspectiva, entrega"), ("A Morte", "fim de ciclo, transformação, renascimento"),
    ("A Temperança", "harmonia, moderação, cura"), ("O Diabo", "apego, tentação, sombra"),
    ("A Torre", "ruptura, revelação, libertação súbita"), ("A Estrela", "esperança, inspiração, renovação"),
    ("A Lua", "ilusão, medo, inconsciente"), ("O Sol", "alegria, sucesso, clareza"),
    ("O Julgamento", "despertar, chamado, renovação"), ("O Mundo", "conclusão, realização, plenitude"),
]
NAIPES = {"Copas": "emoções, amor e relações", "Espadas": "pensamentos, conflitos e decisões",
          "Paus": "ação, energia e projetos", "Ouros": "dinheiro, trabalho e mundo material"}
NUMEROS = [("Ás", "semente, início"), ("Dois", "escolha, parceria"), ("Três", "crescimento, expansão"), ("Quatro", "estabilidade, pausa"),
           ("Cinco", "desafio, perda"), ("Seis", "harmonia, troca"), ("Sete", "avaliação, persistência"), ("Oito", "movimento, esforço"),
           ("Nove", "quase lá, resistência"), ("Dez", "conclusão, excesso"), ("Valete", "mensagem, aprendiz"),
           ("Cavaleiro", "impulso, busca"), ("Rainha", "maturidade, acolhimento"), ("Rei", "domínio, liderança")]
BARALHO = [{"carta": n, "arcano": "maior", "sentido": s} for n, s in MAIORES] + [
    {"carta": f"{num} de {naipe}", "arcano": "menor", "sentido": f"{sn}; tema: {tema}"} for naipe, tema in NAIPES.items() for num, sn in NUMEROS]
assert len(BARALHO) == 78

POSICOES = {1: ["conselho do dia"], 3: ["passado", "presente", "futuro"], 5: ["situação", "desafio", "raiz", "conselho", "tendência"]}


def tirar(n: int = 3, semente: str | None = None) -> list[dict]:
    """Cartas sem repetição; cada uma pode vir invertida. Com semente (ex.: usuário + dia), a carta do dia não muda no mesmo dia."""
    rnd = random.Random(semente) if semente else random.SystemRandom()
    cartas = rnd.sample(BARALHO, n)
    pos = POSICOES.get(n, [f"carta {i + 1}" for i in range(n)])
    return [{**c, "posicao": pos[i], "invertida": rnd.random() < 0.3} for i, c in enumerate(cartas)]
