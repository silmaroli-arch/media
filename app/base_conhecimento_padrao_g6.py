# -*- coding: utf-8 -*-
"""Itens de FAQ de preparo de exames (grupo 6). Sem imports do projeto."""

_RD = "Rede D'Or São Luiz"
_RD_COLPO = (_RD, "https://www.rededorsaoluiz.com.br/exames-e-procedimentos/ginecologia/colposcopia")
_RD_HISTERO = (_RD, "https://www.rededorsaoluiz.com.br/exames-e-procedimentos/ultrassonografia/histeroscopia-com-sem-biopsia")
_RD_PAP = (_RD, "https://www.rededorsaoluiz.com.br/exames-e-procedimentos/analises-clinicas/colpocitologia-oncotica-vaginal-microflora-papanicolau")
_RD_URO = (_RD, "https://www.rededorsaoluiz.com.br/exames-e-procedimentos/urodinamica")
_RD_MAMA = (_RD, "https://www.rededorsaoluiz.com.br/exames-e-procedimentos/biopsias/mamotomia-orientada-por-ultrassonografia")
_RD_EEG = (_RD, "https://www.rededorsaoluiz.com.br/exames-e-procedimentos/eletroneurofisiologia/eletrencefalograma-com-mapeamento-cerebral")
_RD_PSG = (_RD, "https://www.rededorsaoluiz.com.br/exames-e-procedimentos/medicina-sono/polissonografia-noite-inteira")
_ABC = ("ABC Med", "https://www.abc.med.br/exames-e-procedimentos/cistoscopia-o-que-e-e-como-se-realiza-qual-e-o-preparo-para-o-exame-para-que-serve-existem-riscos")
_AMAIS = ("a+ Medicina Diagnóstica", "https://www.amaissaude.com.br/sp/exames/biopsia-transretal-prostata")
_ACC_BRONCO = ("A.C.Camargo Cancer Center", "https://accamargo.org.br/pacientes/preparo-de-exames/broncoscopia")
_ACC_ESPIRO = ("A.C.Camargo Cancer Center", "https://accamargo.org.br/pacientes/preparo-de-exames/prova-de-funcao-pulmonar-completa-ou-espirometria")
_VS_PSG = ("Vale Saúde", "https://www.valesaude.com.br/exames/polissonografia/")
_VS_EEG = ("Vale Saúde", "https://www.valesaude.com.br/exames/eletroencefalografia/")
_VS_AUDIO = ("Vale Saúde", "https://www.valesaude.com.br/exames/audiometria/")
_VS_RETINA = ("Vale Saúde", "https://www.valesaude.com.br/exames/mapeamento-de-retina/")
_COA = ("COA Oftalmologia", "https://coa.com.br/exames-oftalmologicos/angiofluoresceinografia/")
_DOUVIR = ("Direito de Ouvir", "https://www.direitodeouvir.com.br/blog/bera-exame-auditivo")
_DASA = ("Nav Dasa", "https://nav.dasa.com.br/blog/nasofibrolaringoscopia")

ITENS = [
    # ---------------- Colposcopia ----------------
    ("Colposcopia", "Posso fazer a colposcopia menstruada?",
     "O ideal é não estar menstruada, porque o sangue atrapalha a visualização do colo do útero. Se a sua menstruação coincidir com a data marcada, avise a equipe da clínica para combinarem o que fazer.",
     _RD_COLPO),
    ("Colposcopia", "Preciso evitar relação sexual, ducha ou creme vaginal antes da colposcopia?",
     "Sim, o preparo costuma pedir que você fique sem relação sexual, sem duchas e sem medicamentos vaginais durante o período indicado nas suas orientações. Esses itens podem esconder alterações e prejudicar a avaliação. Siga o seu preparo com atenção.",
     _RD_COLPO),
    ("Colposcopia", "A colposcopia dói?",
     "Em geral não dói. O incômodo lembra o do exame preventivo. Se for feita uma biópsia, você pode sentir um leve desconforto ou uma cólica passageira.",
     _RD_COLPO),
    ("Colposcopia", "O que esperar depois da colposcopia e quando devo procurar atendimento?",
     "Se não houver biópsia, você costuma voltar à rotina logo em seguida. Com biópsia, pequeno sangramento, corrimento escuro e cólica são esperados e passam rápido, e a equipe orienta quando retomar as relações. Sangramento intenso, dor forte ou febre pedem atendimento.",
     _RD_COLPO),

    # ---------------- Histeroscopia ----------------
    ("Histeroscopia", "Posso fazer a histeroscopia durante a menstruação?",
     "Normalmente o exame é marcado em uma fase do ciclo fora da menstruação, escolhida pela equipe. Se a menstruação descer na data agendada, avise a clínica para ver a melhor solução.",
     _RD_HISTERO),
    ("Histeroscopia", "Preciso ficar em jejum para a histeroscopia?",
     "Só há jejum quando o exame é feito com sedação ou anestesia, e nesse caso ele deve ser cumprido exatamente pelo tempo indicado no seu preparo. Sem sedação, em geral não é preciso ficar em jejum. Se tiver dúvida sobre o seu caso, pergunte à equipe da clínica.",
     _RD_HISTERO),
    ("Histeroscopia", "Como devo me preparar antes de ir fazer a histeroscopia?",
     "Esvazie a bexiga antes de começar e use uma roupa fácil de tirar. Evite cremes vaginais, duchas e relação sexual pelo período indicado no seu preparo. Avise a equipe sobre os medicamentos que usa, principalmente anticoagulantes, e não mude nenhum por conta própria.",
     _RD_HISTERO),
    ("Histeroscopia", "Como fico depois da histeroscopia?",
     "Cólica leve e um pequeno sangramento são comuns, e o uso de absorvente externo ajuda. Se foi feita biópsia, o material segue para análise e o resultado vem depois. Se a dor ou o sangramento forem fortes, ou se aparecer febre, avise a equipe ou procure atendimento.",
     _RD_HISTERO),

    # ---------------- Citologia oncótica (Papanicolau) ----------------
    ("Citologia oncótica (Papanicolau)", "Como me preparar para o exame preventivo (Papanicolau)?",
     "O preparo é simples. Nos períodos indicados no seu preparo, evite relação sexual, duchas vaginais e o uso de cremes, lubrificantes ou medicamentos vaginais. Assim a amostra fica de boa qualidade e o resultado mais confiável.",
     _RD_PAP),
    ("Citologia oncótica (Papanicolau)", "Posso fazer o Papanicolau menstruada?",
     "Sempre que possível, agende para um período em que você não esteja menstruada, pois o sangue pode atrapalhar a leitura da amostra. Se não der para evitar a coincidência, avise a equipe da clínica.",
     _RD_PAP),
    ("Citologia oncótica (Papanicolau)", "Preciso de algum preparo especial além dos cuidados com relação e duchas?",
     "Em geral não. O exame não exige uma preparação complexa, basta seguir os cuidados que constam no seu preparo. Chegue com a orientação em mãos e avise a equipe sobre qualquer medicamento vaginal em uso.",
     _RD_PAP),

    # ---------------- Urodinâmica ----------------
    ("Urodinâmica", "Preciso chegar com a bexiga cheia para a urodinâmica?",
     "Sim, a orientação costuma ser tomar bastante água antes e chegar com a bexiga cheia. Siga a instrução que veio no seu preparo, pois a clínica pode ter uma orientação específica.",
     _RD_URO),
    ("Urodinâmica", "Preciso de jejum ou de mudar meus remédios para a urodinâmica?",
     "Em geral o exame não exige jejum. Quanto aos medicamentos, não faça mudanças por conta própria. Avise a equipe sobre tudo o que você usa e siga o seu preparo.",
     _RD_URO),
    ("Urodinâmica", "Posso fazer a urodinâmica menstruada?",
     "Não é recomendado fazer o exame durante a menstruação. Se isso for acontecer na data marcada, avise a clínica antes para reorganizarem o seu horário.",
     _RD_URO),
    ("Urodinâmica", "A urodinâmica dói? Preciso de acompanhante?",
     "O exame não costuma doer, mas pode causar um leve desconforto quando as sondas finas são colocadas, e é usado gel anestésico local. A orientação é levar um acompanhante. Se ficar nervosa, converse com a equipe durante o exame.",
     _RD_URO),

    # ---------------- Cistoscopia ----------------
    ("Cistoscopia", "Preciso ficar em jejum para a cistoscopia?",
     "Costuma haver orientação de jejum, e o tempo exato depende do seu preparo. Siga exatamente o que foi informado, principalmente se houver sedação ou anestesia.",
     _ABC),
    ("Cistoscopia", "Uso anticoagulante, o que faço antes da cistoscopia?",
     "Avise a equipe da clínica que você usa anticoagulante ou outros remédios que afetam a coagulação. Não pare nem continue o medicamento por conta própria, siga o que estiver no seu preparo.",
     _ABC),
    ("Cistoscopia", "Preciso fazer exame de urina antes da cistoscopia?",
     "Muitas vezes sim. Uma infecção urinária pode impedir a realização do exame, por isso avise a equipe se sentir ardência, vontade frequente de urinar ou febre antes da data marcada.",
     _ABC),
    ("Cistoscopia", "Como fico depois da cistoscopia e quando devo me preocupar?",
     "É comum sentir desconforto ao urinar e ver um pouco de sangue na urina, o que deve passar rapidamente. Você costuma ir para casa no mesmo dia. Sangramento em excesso ou sinais de infecção, como febre, pedem contato com a equipe ou atendimento.",
     _ABC),

    # ---------------- Biópsia de próstata ----------------
    ("Biópsia de próstata", "Como é o jejum e a limpeza intestinal para a biópsia de próstata?",
     "O preparo costuma incluir jejum e uma lavagem intestinal com um produto de uso retal antes do exame. Faça tudo do jeito e no tempo indicados no seu preparo. Se você tem pressão alta, avise a equipe, pois a orientação pode ser diferente.",
     _AMAIS),
    ("Biópsia de próstata", "Tomo remédio que afina o sangue, posso fazer a biópsia de próstata?",
     "Anticoagulantes e antiplaquetários precisam de orientação individual antes do exame. Não pare nem mantenha por conta própria, avise a equipe da clínica e siga o seu preparo.",
     _AMAIS),
    ("Biópsia de próstata", "Vou tomar antibiótico na biópsia de próstata?",
     "Costuma-se usar antibiótico no momento do exame para diminuir o risco de infecção. Avise a equipe se você tiver alergia a algum antibiótico, pois existem alternativas.",
     _AMAIS),
    ("Biópsia de próstata", "Preciso de acompanhante e posso dirigir depois da biópsia de próstata?",
     "A presença de um acompanhante costuma ser obrigatória, e o exame não é compatível com dirigir logo depois. Um pequeno sangramento nos períodos seguintes é comum. Se houver sinais de infecção, febre ou sangramento importante, procure o pronto-socorro.",
     _AMAIS),

    # ---------------- Biópsia de mama guiada ----------------
    ("Biópsia de mama guiada", "Preciso ficar em jejum para a biópsia de mama?",
     "Em geral não é necessário. O jejum só é pedido quando o procedimento é feito com sedação ou anestesia geral, e o tempo é informado no seu preparo. O tipo de anestesia depende da avaliação da equipe.",
     _RD_MAMA),
    ("Biópsia de mama guiada", "Uso aspirina ou anticoagulante, o que faço antes da biópsia de mama?",
     "Esses medicamentos aumentam o risco de sangramento e a equipe dá uma orientação própria sobre eles, antes e depois do exame. Não mude nada por conta própria, avise a clínica e siga o seu preparo.",
     _RD_MAMA),
    ("Biópsia de mama guiada", "Preciso levar acompanhante para a biópsia de mama?",
     "Sim, a clínica costuma exigir um acompanhante maior de idade durante todo o procedimento. Combine com antecedência para não correr o risco de o exame ser remarcado.",
     _RD_MAMA),
    ("Biópsia de mama guiada", "Posso usar creme ou desodorante antes da biópsia de mama?",
     "O melhor é evitar cremes e desodorantes nas mamas e nas axilas antes do exame. Depois, você fica um período em observação e o material vai para análise. Avise a equipe se notar sangramento fora do normal ou sinais de infecção.",
     _RD_MAMA),

    # ---------------- Broncoscopia ----------------
    ("Broncoscopia", "Preciso ficar em jejum para a broncoscopia?",
     "Sim, o jejum costuma ser absoluto e deve ser cumprido pelo tempo indicado no seu preparo. Ele é importante para a sua segurança durante a sedação.",
     _ACC_BRONCO),
    ("Broncoscopia", "Uso anticoagulante, como fica a broncoscopia?",
     "Cada medicamento que afeta a coagulação tem uma orientação própria de pausa, definida pela equipe. Não pare nem continue por conta própria, informe tudo o que você usa e siga o seu preparo.",
     _ACC_BRONCO),
    ("Broncoscopia", "Preciso de acompanhante e posso dirigir depois da broncoscopia?",
     "Sim, é preciso um acompanhante adulto, pois os sedativos deixam sonolência e afetam o equilíbrio. Não dirija no dia do exame.",
     _ACC_BRONCO),
    ("Broncoscopia", "Como fico depois da broncoscopia e quando devo procurar atendimento?",
     "Você fica em repouso por um tempo curto e depois pode fazer um lanche leve. O normal é voltar às atividades apenas no dia seguinte. Falta de ar, dor no peito, escarro com muito sangue ou febre que não passa exigem atendimento imediato.",
     _ACC_BRONCO),

    # ---------------- Espirometria ----------------
    ("Espirometria", "Posso comer ou tomar café antes da espirometria?",
     "Prefira alimentos leves e evite café, chás, refrigerantes e chocolate pelo período indicado no seu preparo, pois a cafeína pode interferir no teste. Se houver jejum curto, siga o que foi informado.",
     _ACC_ESPIRO),
    ("Espirometria", "Uso bombinha ou broncodilatador, o que devo fazer antes da espirometria?",
     "Esses remédios podem alterar o resultado, e o seu preparo costuma trazer uma orientação sobre eles. Não decida sozinho, avise a equipe quais você usa e quando foi a última vez. Siga o que estiver escrito no seu preparo.",
     _ACC_ESPIRO),
    ("Espirometria", "Posso fumar antes da espirometria?",
     "Fumantes devem ficar sem fumar por um período antes do exame, que consta no seu preparo. Fumar antes pode atrapalhar a medida da função do pulmão.",
     _ACC_ESPIRO),
    ("Espirometria", "Como é a espirometria e o que preciso fazer?",
     "Você respira dentro de um aparelho que mede a função dos pulmões, seguindo as instruções do técnico. Chegue com a antecedência indicada no comprovante para a abertura da ficha e a triagem.",
     _ACC_ESPIRO),

    # ---------------- Polissonografia ----------------
    ("Polissonografia", "Posso tomar café ou bebida alcoólica antes da polissonografia?",
     "Evite bebidas alcoólicas e produtos com cafeína pelo período indicado no seu preparo, pois eles alteram o sono e podem prejudicar o exame. Isso inclui café, energéticos e alguns refrigerantes.",
     _VS_PSG),
    ("Polissonografia", "Como devo estar de cabelo, pele e unhas para a polissonografia?",
     "Lave o couro cabeludo com xampu neutro e evite cosméticos. A pele deve estar limpa, sem cremes, óleos, gel ou maquiagem, para os eletrodos fixarem bem. Evite esmalte escuro nas unhas.",
     _VS_PSG),
    ("Polissonografia", "O que devo levar para dormir na clínica na polissonografia?",
     "Leve pijama ou roupa confortável, escova de dentes e objetos pessoais. Se você tem dificuldade para dormir fora de casa, pode levar o seu travesseiro. Você pode se levantar para ir ao banheiro quando precisar.",
     _VS_PSG),
    ("Polissonografia", "E se eu estiver gripada ou usar remédios de rotina antes da polissonografia?",
     "Avise a equipe sobre todos os medicamentos que usa e não mude nada por conta própria, siga o seu preparo. Se estiver com tosse, gripe ou resfriado perto do exame, informe a clínica, pois isso pode prejudicar o sono e levar ao reagendamento.",
     _VS_PSG),

    # ---------------- Eletroencefalograma ----------------
    ("Eletroencefalograma", "Como devo estar de cabelo para o eletroencefalograma?",
     "Vá com o cabelo limpo e seco, sem cremes, gel ou mousse. Não é preciso raspar. O ideal é lavar com xampu neutro, sem condicionador. Se fez progressiva ou relaxamento recentemente, avise a equipe, pois pode ser preciso esperar um pouco.",
     _VS_EEG),
    ("Eletroencefalograma", "Preciso dormir menos antes do eletroencefalograma?",
     "Alguns exames pedem que você durma menos que o habitual, ou fique acordado pelo maior tempo possível, para conseguir dormir durante o teste. Isso depende do tipo de exame, então siga exatamente o que está no seu preparo.",
     _VS_EEG),
    ("Eletroencefalograma", "Posso comer e tomar café antes do eletroencefalograma?",
     "Em geral não há jejum, e é melhor ir bem alimentado. Evite café, energéticos e outras bebidas estimulantes pelo período indicado no seu preparo.",
     _RD_EEG),
    ("Eletroencefalograma", "Tomo anticonvulsivante ou outros remédios, o que faço antes do eletroencefalograma?",
     "Não mude nenhum medicamento por conta própria. Avise a equipe sobre tudo o que você usa e siga o seu preparo, pois as orientações podem variar entre as unidades.",
     _RD_EEG),
    ("Eletroencefalograma", "Como fico depois do eletroencefalograma?",
     "O exame é indolor e, em geral, você volta às atividades normais. Se ficou com privação de sono, vá acompanhado e evite dirigir depois do exame.",
     _VS_EEG),

    # ---------------- Mapeamento de retina e angiofluoresceinografia ----------------
    ("Mapeamento de retina e angiofluoresceinografia", "Vou dilatar a pupila, posso dirigir depois do mapeamento de retina?",
     "Não é indicado. Os colírios deixam a visão embaçada por um período, e nesse tempo é melhor não dirigir nem andar sozinho. Óculos escuros ajudam a suportar a claridade.",
     _VS_RETINA),
    ("Mapeamento de retina e angiofluoresceinografia", "Posso usar lente de contato e preciso de acompanhante no mapeamento de retina?",
     "Evite lentes de contato no dia do exame. Vá acompanhado, pois depois da dilatação a visão fica prejudicada por um tempo e você pode precisar de ajuda. Leve óculos escuros.",
     _VS_RETINA),
    ("Mapeamento de retina e angiofluoresceinografia", "Preciso de jejum para a angiofluoresceinografia?",
     "Depende da clínica. Alguns serviços pedem jejum curto e outros apenas que você evite refeições pesadas. Siga o que está no seu preparo.",
     _COA),
    ("Mapeamento de retina e angiofluoresceinografia", "O que vou sentir com o contraste da angiofluoresceinografia?",
     "Você pode sentir calor, gosto metálico ou um enjoo passageiro, e isso costuma passar rápido. A pele e a urina podem ficar amareladas por um tempo, o que é esperado e temporário.",
     _COA),
    ("Mapeamento de retina e angiofluoresceinografia", "Tenho alergia ou estou grávida, o que informo antes da angiofluoresceinografia?",
     "Avise a equipe sobre qualquer alergia, os medicamentos que usa e uma possível gravidez, pois o contraste pode exigir cuidado especial. Reações alérgicas graves são raras, e a equipe acompanha você durante o exame.",
     _COA),

    # ---------------- Audiometria e BERA ----------------
    ("Audiometria e BERA", "Preciso de repouso auditivo antes da audiometria?",
     "Sim, evite ruídos fortes e constantes pelo período indicado no seu preparo, pois eles podem alterar temporariamente o resultado. Também procure dormir bem na noite anterior.",
     _VS_AUDIO),
    ("Audiometria e BERA", "O que devo avisar antes da audiometria?",
     "Informe a equipe sobre os medicamentos que usa, infecções de ouvido, perfuração de tímpano ou qualquer problema no ouvido que possa atrapalhar a passagem do som. O exame é simples e indolor.",
     _VS_AUDIO),
    ("Audiometria e BERA", "Como devo ir de cabelo e pele para o BERA?",
     "Vá com o cabelo limpo e seco, sem produtos de modelagem. A pele é limpa com um produto próprio antes de fixar os eletrodos, então não é preciso preparar nada em casa.",
     _DOUVIR),
    ("Audiometria e BERA", "Meu filho precisa dormir para fazer o BERA?",
     "Em crianças o teste costuma ser feito durante o sono natural, e em alguns casos a equipe induz o sono para o exame correr com tranquilidade. Um acompanhante pode ficar junto da criança. Siga o seu preparo para saber o que levar.",
     _DOUVIR),
    ("Audiometria e BERA", "Como é o BERA e como fico depois?",
     "Você fica deitado, de olhos fechados, com fones de ouvido e o mais relaxado possível. Adultos devem evitar contrair o pescoço e o rosto. O exame não dói e depois você retoma as atividades normalmente.",
     _DOUVIR),

    # ---------------- Nasofibrolaringoscopia ----------------
    ("Nasofibrolaringoscopia", "Preciso ficar em jejum para a nasofibrolaringoscopia?",
     "Alguns serviços pedem jejum por causa do pequeno risco de enjoo, outros não. Siga o que estiver no seu preparo.",
     _DASA),
    ("Nasofibrolaringoscopia", "Tenho alergia a anestésico, o que devo avisar antes da nasofibrolaringoscopia?",
     "Avise a equipe sobre qualquer alergia a medicamentos, pois um spray anestésico pode ser usado no nariz para diminuir o incômodo.",
     _DASA),
    ("Nasofibrolaringoscopia", "Como é a nasofibrolaringoscopia e o que vou sentir?",
     "Um tubo fino e flexível passa pelo nariz para observar a garganta, e o exame é rápido. Pode haver algum incômodo, mas costuma ser bem tolerado. Em geral não há restrições depois, e você segue a sua rotina.",
     _DASA),
]
