"""Criptografia simétrica (Fernet) para o conteúdo clínico do paciente:
o texto de cada evolução clínica (ver EvolucaoClinica em app/models.py) e a
senha do certificado digital pessoal do médico (usado para assinar as
evoluções — ver app/assinatura_clinica.py). Separado de app/cripto_fiscal.py
de propósito: são domínios de dado diferentes (clínico vs. fiscal/financeiro),
então cada um tem sua própria chave — comprometer uma não expõe a outra.

A chave vem da variável de ambiente CHAVE_CRIPTOGRAFIA_CLINICA (uma chave
Fernet — 32 bytes urlsafe-base64, gerada com `Fernet.generate_key()`),
configurada separadamente em cada ambiente do Elastic Beanstalk
(media-dev/media-qa/media-prod) nas variáveis de ambiente do console AWS —
nunca commitada no repositório. Trocar essa chave depois de já existirem
dados salvos torna esses dados antigos ilegíveis (as evoluções clínicas e a
senha do certificado do médico precisariam ser recadastradas).
"""
import os
import warnings

from cryptography.fernet import Fernet, InvalidToken

_fernet = None


def _obter_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet

    chave = os.environ.get("CHAVE_CRIPTOGRAFIA_CLINICA")
    if not chave:
        warnings.warn(
            "CHAVE_CRIPTOGRAFIA_CLINICA não definida. Usando uma chave "
            "gerada temporariamente só para não quebrar o desenvolvimento "
            "local — configure essa variável de ambiente no Elastic "
            "Beanstalk antes de usar o prontuário em produção. Sem uma "
            "chave fixa, tudo que for criptografado agora (evoluções "
            "clínicas, certificado do médico) fica ilegível depois de "
            "reiniciar a aplicação."
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
