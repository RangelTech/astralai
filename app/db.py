"""Banco do Astral AI: SQLite num volume (/data). Hora em epoch (s)."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, criado REAL NOT NULL,
  plano TEXT NOT NULL DEFAULT 'teste', ativo_ate REAL NOT NULL DEFAULT 0, pedido TEXT);
CREATE TABLE IF NOT EXISTS codigos (email TEXT PRIMARY KEY, hash TEXT NOT NULL, expira REAL NOT NULL, tentativas INTEGER NOT NULL DEFAULT 0, enviado REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessoes (hash TEXT PRIMARY KEY, usuario INTEGER NOT NULL, expira REAL NOT NULL);
CREATE TABLE IF NOT EXISTS eventos_cakto (id TEXT PRIMARY KEY, recebido REAL NOT NULL, corpo TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assinaturas (usuario INTEGER PRIMARY KEY, plano TEXT NOT NULL, ativo_ate REAL NOT NULL, pedido TEXT);

-- perfis natais: a própria pessoa (principal=1) e quem ela quiser (mãe, parceiro... para sinastria)
CREATE TABLE IF NOT EXISTS perfis (
  id INTEGER PRIMARY KEY, usuario INTEGER NOT NULL, principal INTEGER NOT NULL DEFAULT 0, nome TEXT NOT NULL,
  nascimento TEXT NOT NULL, hora TEXT NOT NULL DEFAULT '', cidade TEXT NOT NULL DEFAULT '',
  lat REAL, lon REAL, fuso TEXT, numerologia TEXT, mapa TEXT, criado REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS perfis_usuario ON perfis(usuario);

-- conversa com a IA e leituras (tarô, mapa, sinastria) guardadas para o histórico
CREATE TABLE IF NOT EXISTS mensagens (id INTEGER PRIMARY KEY, usuario INTEGER NOT NULL, papel TEXT NOT NULL, texto TEXT NOT NULL, criado REAL NOT NULL);
CREATE INDEX IF NOT EXISTS mensagens_usuario ON mensagens(usuario, criado);
CREATE TABLE IF NOT EXISTS leituras (id INTEGER PRIMARY KEY, usuario INTEGER NOT NULL, tipo TEXT NOT NULL, dados TEXT NOT NULL,
  texto TEXT NOT NULL, criado REAL NOT NULL);
CREATE INDEX IF NOT EXISTS leituras_usuario ON leituras(usuario, criado);
"""


class Banco:
    def __init__(self, caminho: str):
        self._trava = threading.Lock()
        self.con = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(ESQUEMA)

    @contextmanager
    def tx(self):
        with self._trava:
            self.con.execute("BEGIN IMMEDIATE")
            try:
                yield self.con
                self.con.execute("COMMIT")
            except BaseException:
                self.con.execute("ROLLBACK")
                raise

    def exec(self, sql: str, args: tuple = ()):
        with self._trava:
            return self.con.execute(sql, args)

    def um(self, sql: str, args: tuple = ()) -> dict | None:
        with self._trava:
            r = self.con.execute(sql, args).fetchone()
        return dict(r) if r else None

    def todos(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._trava:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]


def agora() -> float:
    return time.time()
