"""Criptografia simétrica (Fernet) para dados fiscais sensíveis da clínica:
senha do certificado digital A1 (.pfx), token/API key do provedor de NFC-e e
o código CSC. Esses valores nunca são gravados em texto puro no banco — só a
versão criptografada (colunas `*_cripto` em `Clinica`, ver app/models.py).

A chave vem da variável de ambiente CHAVE_CRIPTOGRAFIA_FISCAL (uma chave
Fernet — 32 bytes urlsafe-base64, gerada com `Fernet.generate_key()`),
configurada separadamente em cada ambiente do Elastic Beanstalk
(media-dev/media-qa/media-prod) nas variáveis de ambiente do console AWS —
nunca commitada no repositório. Trocar essa chave depois de já existirem
dados salvos torna esses dados antigos ilegíveis (a senha do certificado e o
token precisariam ser reenviados).
"""
import os
import warnings

from cryptography.fernet import Fernet, InvalidToken

_fernet = None


def _obter_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet

    chave = os.environ.get("CHAVE_CRIPTOGRAFIA_FISCAL")
    if not chave:
        warnings.warn(
            "CHAVE_CRIPTOGRAFIA_FISCAL não definida. Usando uma chave "
            "gerada temporariamente só para não quebrar o desenvolvimento "
            "local — configure essa variável de ambiente no Elastic "
            "Beanstalk antes de usar a emissão fiscal em produção. Sem uma "
            "chave fixa, tudo que for criptografado agora fica ilegível "
            "depois de reiniciar a aplicação."
        )
        chave = Fernet.generate_key()
    elif isinstance(chave, str):
        chave = chave.encode("utf-8")

    _fernet = Fernet(chave)
    return _fernet


def criptografar_bytes(dados):
    """Recebe bytes (ou None) e devolve os bytes criptografados (ou None)."""
    if not dados:
        return None
    return _obter_fernet().encrypt(dados)


def descriptografar_bytes(dados):
    """Recebe os bytes criptografados vindos do banco e devolve os bytes
    originais, ou None se estiver vazio ou não puder ser decifrado (ex.: a
    chave de criptografia mudou)."""
    if not dados:
        return None
    try:
        return _obter_fernet().decrypt(bytes(dados))
    except InvalidToken:
        return None


def criptografar_texto(texto):
    if not texto:
        return None
    return criptografar_bytes(texto.encode("utf-8"))


def descriptografar_texto(dados):
    resultado = descriptografar_bytes(dados)
    if resultado is None:
        return None
    return resultado.decode("utf-8")
