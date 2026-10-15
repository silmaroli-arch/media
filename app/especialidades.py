"""Lista de especialidades médicas do dropdown do cadastro do médico (pedido
do Silvan, 2026-09-29). Serve para decidir qual parte da base de
conhecimento compartilhada o médico enxerga (casa por nome com
TipoExame.especialidades - por isso os nomes aqui devem ser os mesmos usados
em app/tipos_exame_padrao.py)."""

ESPECIALIDADES = [
    "Anestesiologia", "Cardiologia", "Cirurgia geral", "Clínica médica", "Coloproctologia",
    "Dermatologia", "Endocrinologia", "Gastroenterologia", "Geriatria", "Ginecologia e obstetrícia",
    "Hematologia", "Infectologia", "Mastologia", "Medicina nuclear", "Nefrologia", "Neurologia",
    "Oftalmologia", "Ortopedia", "Otorrinolaringologia", "Patologia clínica e laboratório", "Pediatria",
    "Pneumologia", "Radiologia e diagnóstico por imagem", "Reumatologia", "Urologia", "Outra",
]


def especialidade_valida(valor):
    """Devolve o valor limpo se estiver na lista, senão None (campo opcional)."""
    valor = (valor or "").strip()
    return valor if valor in ESPECIALIDADES else None
