# -*- coding: utf-8 -*-
"""Itens de FAQ de preparo (grupo 4): exames cardiologicos."""

_TEE = ("Rede D'Or São Luiz - Ecodopplercardiograma transesofágico", "https://www.rededorsaoluiz.com.br/exames-e-procedimentos/ecocardiograma/ecodopplercardiograma-transesofagico")
_ERGO = ("Rede D'Or São Luiz - Teste ergométrico", "https://www.rededorsaoluiz.com.br/exames-e-procedimentos/cardiologicos/teste-ergometrico")
_ESTRESSE = ("Fleury - Ecocardiograma com estresse farmacológico", "https://www.fleury.com.br/exames/ecocardiograma-com-estresse-farmacologico")
_CATE = ("Rede D'Or São Luiz - Cateterismo cardíaco", "https://www.rededorsaoluiz.com.br/noticias/artigo/o-que-e-cateterismo-cardiaco-e-como-e-realizado")
_TILT_HCOR = ("HCor - Tilt-test", "https://www.hcor.com.br/exames-e-consultas/exames-diagnosticos/tilt-test/")
_TILT_DOR = ("Rede D'Or São Luiz - Tilt teste", "https://www.rededorsaoluiz.com.br/exames-e-procedimentos/cardiologicos/tilt-teste")

ITENS = [
    # Ecocardiograma transesofágico
    ("Ecocardiograma transesofágico", "Preciso ficar em jejum para o ecocardiograma transesofágico?",
     "Sim, esse exame exige estômago vazio para que a passagem do aparelho seja segura e confortável. Siga o tempo de jejum que está no seu preparo, sem comer sólidos nesse período. Se tiver dúvida sobre líquidos, pergunte à equipe da clínica.", _TEE),
    ("Ecocardiograma transesofágico", "Posso ir sozinho fazer o ecocardiograma transesofágico?",
     "O ideal é ir com um acompanhante adulto, que fica na clínica durante todo o exame. Como você recebe uma sedação leve, não deve dirigir depois. Combine a volta para casa com antecedência.", _TEE),
    ("Ecocardiograma transesofágico", "Vou sentir dor quando o aparelho passar pela garganta?",
     "A garganta recebe uma anestesia local e você fica com sedação leve, então o exame costuma ser bem tolerado. É comum sentir um incômodo ou vontade de tossir quando o tubo passa. Dor forte é incomum, e se algo incomodar muito, sinalize para a equipe.", _TEE),
    ("Ecocardiograma transesofágico", "Posso tomar meus remédios de rotina antes do ecocardiograma transesofágico?",
     "Não mude nenhum remédio por conta própria. Leve a lista de tudo o que você usa e avise a equipe da clínica, principalmente se usa anticoagulante ou remédio para diabetes. Siga o seu preparo sobre o que tomar antes do exame.", _TEE),
    ("Ecocardiograma transesofágico", "O que acontece depois do ecocardiograma transesofágico?",
     "Você fica em observação até a sedação passar e só sai quando a equipe liberar. A garganta pode ficar levemente irritada por um tempo. Volte a comer somente quando a equipe orientar e avise a clínica se sentir dor forte ao engolir ou algo diferente.", _TEE),

    # Teste ergométrico
    ("Teste ergométrico", "Preciso ir em jejum para o teste ergométrico?",
     "Não, o ideal é não ir em jejum. Faça uma refeição leve antes, no tempo indicado no seu preparo, para evitar mal-estar durante o esforço. Evite também exercícios pesados no dia do exame.", _ERGO),
    ("Teste ergométrico", "Posso tomar café ou fumar antes do teste ergométrico?",
     "Não. No dia do exame, evite café, chá, chocolate, refrigerante e bebida alcoólica, pois podem alterar os batimentos do coração. O cigarro também deve ser evitado pelo tempo indicado no seu preparo.", _ERGO),
    ("Teste ergométrico", "Devo tomar meus remédios no dia do teste ergométrico?",
     "Em geral, os remédios de uso contínuo seguem normalmente, a menos que a orientação do seu preparo diga outra coisa. Alguns remédios para o coração podem influenciar o resultado, então avise a equipe da clínica sobre tudo o que usa. Não suspenda nada por conta própria.", _ERGO),
    ("Teste ergométrico", "Que roupa e calçado devo usar no teste ergométrico?",
     "Vá com roupa de ginástica, como bermuda ou calça confortável, e tênis fechado. Evite salto e sandália. Vale levar uma toalha de mão pequena.", _ERGO),
    ("Teste ergométrico", "O que posso sentir durante o teste ergométrico e preciso de algum cuidado depois?",
     "Você vai caminhar ou correr na esteira com esforço crescente, então é normal sentir cansaço e o coração acelerado. Se sentir dor no peito, tontura ou falta de ar, avise a equipe na hora, ela acompanha você o tempo todo. Depois do exame, em geral não há cuidados especiais.", _ERGO),

    # Ecocardiograma de estresse
    ("Ecocardiograma de estresse", "Preciso de jejum para o ecocardiograma de estresse?",
     "Sim, costuma ser pedido um jejum mínimo antes do exame. Siga o tempo que consta no seu preparo, pois ele pode variar conforme o serviço. Se tiver dúvida sobre água, pergunte à equipe da clínica.", _ESTRESSE),
    ("Ecocardiograma de estresse", "Que remédios devo informar antes do ecocardiograma de estresse?",
     "Faça uma lista de todos os remédios que você usou recentemente e leve no dia do exame. Essa informação é importante para a equipe interpretar o resultado. Não suspenda nem mantenha nada por conta própria, siga o seu preparo.", _ESTRESSE),
    ("Ecocardiograma de estresse", "Posso tomar café antes do ecocardiograma de estresse?",
     "Cafeína pode interferir em exames que estressam o coração. Por isso, veja no seu preparo se há restrição de café, chá, chocolate e refrigerante. Se o preparo não mencionar, pergunte à equipe da clínica antes do exame.", _ESTRESSE),
    ("Ecocardiograma de estresse", "Como é o ecocardiograma de estresse e o que vou sentir?",
     "Fazem-se imagens do coração em repouso e depois com o coração estimulado, por esforço ou por medicação. Você pode sentir o coração acelerado, calor ou cansaço passageiro. Avise a equipe se sentir dor no peito, falta de ar ou tontura.", _ESTRESSE),
    ("Ecocardiograma de estresse", "Quando sai o resultado do ecocardiograma de estresse?",
     "O resultado sai no prazo informado pela clínica no momento do agendamento. Guarde o comprovante de retirada e leve o resultado à sua consulta de retorno. Se tiver algum sintoma depois do exame, avise a equipe da clínica.", _ESTRESSE),

    # Cateterismo cardíaco
    ("Cateterismo cardíaco", "Preciso de jejum para o cateterismo cardíaco?",
     "Sim, em exames programados é preciso ficar em jejum, inclusive de água, pelo tempo indicado no seu preparo. Em situações de urgência essa regra não se aplica. Se tiver problema nos rins, siga com atenção a orientação de hidratação do seu preparo.", _CATE),
    ("Cateterismo cardíaco", "Devo parar meus remédios antes do cateterismo cardíaco?",
     "Não decida isso sozinho. Informe a equipe da clínica sobre todos os remédios que usa, principalmente anticoagulantes e remédios para diabetes, e siga o seu preparo. Quem tem alergia a contraste deve seguir a medicação prévia que foi indicada.", _CATE),
    ("Cateterismo cardíaco", "Que roupa devo levar para o cateterismo cardíaco?",
     "Vá com roupa confortável e folgada. Retire joias, óculos, lentes de contato e outros objetos pessoais antes do exame. Deixe os valores em casa ou com o acompanhante.", _CATE),
    ("Cateterismo cardíaco", "Como é o cateterismo cardíaco, vou sentir dor?",
     "O exame é feito em uma sala especializada, com sedação leve e anestesia local. O cateter entra por uma pequena punção na virilha ou no punho. Você pode sentir pressão ou calor, mas a equipe acompanha você o tempo todo.", _CATE),
    ("Cateterismo cardíaco", "Quais os cuidados depois do cateterismo cardíaco?",
     "Após o exame é colocado um curativo no local da punção, e a região deve ficar imobilizada por um período. Siga as orientações de repouso da equipe. Avise a clínica se notar sangramento, inchaço ou dor forte no local, ou se sentir dor no peito ou falta de ar.", _CATE),

    # Teste de inclinação (tilt test)
    ("Teste de inclinação (tilt test)", "Preciso de jejum para o tilt test?",
     "Sim, o exame pede jejum pelo tempo indicado no seu preparo. Evite comidas pesadas antes disso. Siga a orientação de líquidos que consta no seu preparo.", _TILT_DOR),
    ("Teste de inclinação (tilt test)", "Posso tomar meus remédios de rotina antes do tilt test?",
     "Em geral, os remédios de uso regular seguem normalmente, com o mínimo de água possível, salvo orientação diferente no seu preparo. Alguns medicamentos, como os da classe da semaglutida, podem ter orientação específica. Avise a equipe da clínica sobre tudo o que usa e não suspenda nada por conta própria.", _TILT_HCOR),
    ("Teste de inclinação (tilt test)", "Preciso de acompanhante para o tilt test e posso dirigir depois?",
     "Sim, é necessário um acompanhante adulto, e sem ele o exame pode não ser realizado. Depois do exame, evite dirigir até se sentir totalmente recuperado. Combine a volta para casa com antecedência.", _TILT_DOR),
    ("Teste de inclinação (tilt test)", "Que roupa devo usar no tilt test?",
     "Use roupa confortável e que abra na frente, para facilitar a colocação dos eletrodos. Evite meia-calça. Em alguns casos pede-se retirar os pelos do peito, siga o seu preparo.", _TILT_HCOR),
    ("Teste de inclinação (tilt test)", "O que vou sentir no tilt test e como me cuidar depois?",
     "Você fica deitado numa maca que se inclina, com o coração e a pressão monitorados. Pode surgir tontura, suor ou sensação de desmaio, e a equipe está pronta para ajudar. Depois, descanse se restar tontura ou fraqueza e só coma quando a equipe liberar.", _TILT_DOR),
]
