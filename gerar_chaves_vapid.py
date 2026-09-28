"""Gera o par de chaves VAPID usado pela notificação push do PWA da
equipe (ver app/push_notificacoes.py). Rodar UMA VEZ:

    python gerar_chaves_vapid.py

e colar as 3 linhas impressas nas variáveis de ambiente do Elastic
Beanstalk (mesmo lugar de TWILIO_ACCOUNT_SID etc.) e/ou no seu .env
local. Gerar de novo troca a identidade do servidor perante o navegador -
toda inscrição de push já feita pela equipe (PushSubscription) para de
funcionar e cada pessoa precisa reativar a notificação depois."""
from py_vapid import Vapid02
from py_vapid.utils import b64urlencode

v = Vapid02()
v.generate_keys()

# Chave privada: valor "d" cru, em base64url sem padding - é o formato
# que pywebpush.webpush(vapid_private_key=...) espera quando não é um
# caminho de arquivo PEM (ver py_vapid.Vapid.from_string).
numeros_privados = v.private_key.private_numbers()
chave_privada_b64 = b64urlencode(numeros_privados.private_value.to_bytes(32, "big"))

# Chave pública: ponto EC não comprimido (0x04 + x + y), formato exigido
# pelo PushManager.subscribe({applicationServerKey: ...}) do navegador.
numeros_publicos = v.public_key.public_numbers()
ponto_publico = b"\x04" + numeros_publicos.x.to_bytes(32, "big") + numeros_publicos.y.to_bytes(32, "big")
chave_publica_b64 = b64urlencode(ponto_publico)

print("Cole estas 3 linhas nas variáveis de ambiente (Elastic Beanstalk e/ou .env local):\n")
print(f"VAPID_PUBLIC_KEY={chave_publica_b64}")
print(f"VAPID_PRIVATE_KEY={chave_privada_b64}")
print("VAPID_CLAIM_EMAIL=mailto:contato@inflor.com.br  # troque pelo e-mail de contato real")
