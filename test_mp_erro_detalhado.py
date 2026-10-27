"""O log passa a mostrar o que o Mercado Pago respondeu (2026-10-27)."""
import logging
from types import SimpleNamespace

import requests

from app.mercadopago_integration import _conferir, descrever_erro


def checar(nome, condicao):
    print(f"[{'OK' if condicao else 'FALHOU'}] {nome}")
    assert condicao, nome


class Captura(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens = []

    def emit(self, registro):
        self.mensagens.append(registro.getMessage())


captura = Captura()
logging.getLogger("app.mercadopago_integration").addHandler(captura)

def falha():
    raise requests.HTTPError("400 Client Error")

ruim = SimpleNamespace(status_code=400, text='{"message":"Payer email forbidden","error":"bad_request"}', raise_for_status=falha)
try:
    _conferir(ruim)
    levantou = False
except requests.HTTPError:
    levantou = True
checar("Continua levantando o erro", levantou)
checar("O log traz o status e o corpo da resposta do Mercado Pago",
       any("status=400" in m and "Payer email forbidden" in m for m in captura.mensagens))
ok = SimpleNamespace(status_code=200, text="{}", raise_for_status=lambda: None)
_conferir(ok)
checar("Resposta boa não gera log nem erro", len(captura.mensagens) == 1)
class _Erro(Exception):
    pass


e = _Erro()
e.response = SimpleNamespace(status_code=403, json=lambda: {"message": "Unauthorized use of live credentials"})
checar("Mensagem da tela traz o código e o motivo do Mercado Pago", descrever_erro(e) == " (Mercado Pago: código 403 - Unauthorized use of live credentials)")
checar("Erro que não veio do Mercado Pago (rede) não acrescenta nada", descrever_erro(ValueError("x")) == "")
e.response = SimpleNamespace(status_code=500, json=lambda: (_ for _ in ()).throw(ValueError()))
checar("Corpo ilegível: só o código", descrever_erro(e) == " (Mercado Pago: código 500)")
print("TUDO OK")
