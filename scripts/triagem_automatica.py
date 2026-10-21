#!/usr/bin/env python3
"""RADAR SP: triagem local, reprodutível, SEM API e SEM inferência por LLM.

Lê bases já publicadas em /dados e gera uma fila de revisão rastreável.
NÃO visita textos integrais, NÃO conclui exclusão, NÃO soma orçamento e
NÃO valida evidência em nome de pesquisadoras/es humanos.

Execução: python scripts/triagem_automatica.py
Saída: dados/triagem_automatizada_v01.json

Python 3.11+, somente biblioteca padrão.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DADOS = RAIZ / 'dados'
SAIDA = DADOS / 'triagem_automatizada_v01.json'
ARQUIVOS = {
    'historico': DADOS / 'historico_politicas_sp.json',
    'orcamento': DADOS / 'triagem_orcamento_2026.json',
    'analises': DADOS / 'analises_ia_piloto_v01.json',
    'edicoes': DADOS / 'edicoes_coletadas.json',
}

CATEGORIAS = {
    'marco_institucional': 'Instituição normativa documentada',
    'ampliacao_potencial': 'Previsão de ampliação a verificar',
    'alteracao_indeterminada': 'Alteração sem efeito classificado',
}


def ler_json(rotulo: str, obrigatorio: bool = True):
    arquivo = ARQUIVOS[rotulo]
    if not arquivo.exists():
        if obrigatorio:
            raise FileNotFoundError(f'Base obrigatória ausente: {arquivo}')
        return None, None
    conteudo = arquivo.read_bytes()
    return json.loads(conteudo.decode('utf-8-sig')), sha256(conteudo).hexdigest()


def validar_lista(dados: dict, chave: str, rotulo: str) -> list:
    valores = dados.get(chave)
    if not isinstance(valores, list):
        raise ValueError(f'{rotulo}.{chave} precisa ser lista')
    if not all(isinstance(item, dict) for item in valores):
        raise ValueError(f'{rotulo}.{chave} contém registro inválido')
    return valores


def id_dotacao(item: dict) -> str:
    for chave, valor in item.get('campos', {}).items():
        # Variações de codificação em cabeçalhos anteriores.
        if str(chave).lower().startswith('id dota'):
            return str(valor).strip()
    return ''


def analise_principal() -> dict:
    historico, hash_historico = ler_json('historico')
    orcamento, hash_orcamento = ler_json('orcamento')
    analises, hash_analises = ler_json('analises')
    edicoes, hash_edicoes = ler_json('edicoes', obrigatorio=False)

    eventos = validar_lista(historico, 'eventos', 'historico')
    politicas = validar_lista(historico, 'politicas', 'historico')
    itens_orcamento = validar_lista(orcamento, 'amostra', 'orcamento')
    fichas = validar_lista(analises, 'analises', 'analises')
    edicoes = edicoes if isinstance(edicoes, list) else []

    if len({e.get('id') for e in eventos}) != len(eventos):
        raise ValueError('ID de ato histórico duplicado')
    if len({a.get('id') for a in fichas}) != len(fichas):
        raise ValueError('ID de ficha analítica duplicado')

    inventario_eventos = {str(e.get('id')): e for e in eventos}
    inventario_dotacoes = {id_dotacao(i): i for i in itens_orcamento if id_dotacao(i)}

    normas = []
    for e in sorted(eventos, key=lambda a: (a.get('data_ato') or '', a.get('id') or '')):
        pistas = []
        if not e.get('data_publicacao'):
            pistas.append('Data de publicação no Diário Oficial não verificada')
        if e.get('revisao_cientifica') != 'concluida':
            pistas.append('Revisão científica pendente')
        if not e.get('fonte_oficial'):
            pistas.append('Link oficial ausente')
        if e.get('categoria') not in CATEGORIAS:
            pistas.append('Categoria fora da taxonomia inicial; revisar')
        normas.append({
            'id': e.get('id'),
            'politica_id': e.get('politica_id'),
            'ato': e.get('ato'),
            'data_ato': e.get('data_ato'),
            'data_publicacao': e.get('data_publicacao'),
            'documento_original': e.get('fonte_oficial'),
            'descricao_curada_origem': e.get('descricao_documental'),
            'categoria_origem': e.get('categoria'),
            'etiqueta_automatica': CATEGORIAS.get(e.get('categoria'), 'Categoria a revisar'),
            'limites_herdados': e.get('limite_da_evidencia'),
            'pendencias_revisao': pistas,
            'resultado_empirico_comprovado': False,
            'status': 'pré-triagem sem verificação independente do ato',
        })

    fila = []
    novos_documentos = set()
    for a in fichas:
        ids_historico = [str(x) for x in a.get('ids_eventos', [])]
        ids_financeiros = [str(x) for x in a.get('ids_dotacao', [])]
        nao_encontrados_normas = [x for x in ids_historico if x not in inventario_eventos]
        nao_encontrados_orcamento = [x for x in ids_financeiros if x not in inventario_dotacoes]
        atos_externos = list(a.get('documentos_novos', []))
        novos_documentos.update(atos_externos)
        pendencias = []
        if atos_externos:
            pendencias.append('Incorporar atos adicionais ao inventário histórico e verificar vigência')
        if nao_encontrados_normas:
            pendencias.append('Reconciliar identificadores de normas ausentes')
        if nao_encontrados_orcamento:
            pendencias.append('Reconciliar identificadores de dotações ausentes')
        if ids_financeiros:
            pendencias.append('Confirmar órgão, programa, ação, elemento e fonte da dotação; não somar')
        if a.get('estado') != 'validado_por_pesquisador':
            pendencias.append('Submeter a ficha à revisão humana')
        fila.append({
            'ficha': a.get('id'),
            'titulo': a.get('titulo'),
            'tema': a.get('tema'),
            'prioridade_curada_origem': a.get('prioridade'),
            'nivel_evidencia_declarado_origem': a.get('evidencia'),
            'status': 'hipótese de IA existente; não reanalisada por modelo',
            'normas_referenciadas': ids_historico,
            'normas_ausentes': nao_encontrados_normas,
            'dotacoes_referenciadas': ids_financeiros,
            'dotacoes_ausentes': nao_encontrados_orcamento,
            'atos_novos_indicados_na_ficha': atos_externos,
            'tarefas_de_revisao': pendencias,
            'impacto_real_comprovado': False,
            'irregularidade_confirmada': False,
        })

    categorias_orc = Counter()
    lista_financeira = []
    for i in itens_orcamento:
        grupos = list(i.get('grupos_indicio', []))
        categorias_orc.update(grupos)
        lista_financeira.append({
            'id_dotacao_bruto': id_dotacao(i) or None,
            'linha_csv': i.get('linha_csv'),
            'grupos_por_descricao': grupos,
            'valores_brutos': i.get('valores_brutos_por_linha', {}),
            'atribuicao_a_politica_validada': False,
            'observacao': 'Correspondência textual; nenhuma soma ou efeito inferido',
        })

    erros = []
    if len(inventario_dotacoes) != len(itens_orcamento):
        erros.append('Algumas dotações não têm identificador único recuperável')
    if orcamento.get('totais_orcamentarios_calculados') is True:
        erros.append('Base de entrada declara cálculo de totais; conferir metodologia manualmente')
    if any('ï¿½' in str(x) for x in orcamento.get('cabecalhos', [])):
        erros.append('Cabeçalhos do orçamento apresentam caracteres corrompidos')

    return {
        'esquema': 'radar.triagem_automatica.v0.1',
        'metodo': 'Regras Python determinísticas, sem API de IA, sem inferência por modelo',
        'uso_cientifico': 'Fila de revisão e inventário documental; não prova impacto ou irregularidade',
        'entradas': [
            {'arquivo': str(ARQUIVOS[k].relative_to(RAIZ)), 'sha256': h}
            for k, h in [('historico', hash_historico), ('orcamento', hash_orcamento),
                         ('analises', hash_analises), ('edicoes', hash_edicoes)] if h
        ],
        'resumo': {
            'politicas_no_inventario': len(politicas),
            'atos_historicos_catalogados': len(eventos),
            'fichas_ia_anteriores_verificadas': len(fichas),
            'registros_de_orcamento_candidatos': len(itens_orcamento),
            'contagens_por_grupo_orcamentario': dict(sorted(categorias_orc.items())),
            'atos_adicionais_a_importar': sorted(novos_documentos),
            'edicoes_diario_oficial_registradas': len(edicoes),
            'atos_extraidos_dessas_edicoes': all(x.get('atos_extraidos') is True for x in edicoes) if edicoes else False,
            'efeitos_reais_comprovados_por_este_programa': 0,
        },
        'revisao_documental': normas,
        'fila_fichas_ia': fila,
        'candidatos_orcamentarios': lista_financeira,
        'alertas_tecnicos': erros,
        'limites': [
            'Só examina os metadados e resumos existentes no repositório; não lê os textos integrais das normas.',
            'Não detecta atos publicados depois do corte; precisa de importação separada do Diário Oficial.',
            'Não compara automaticamente redações artigo a artigo nem decide se houve ganho ou perda.',
            'Não calcula valores totais, variação percentual, evolução histórica financeira nem execução atribuível.',
            'Dotações textualmente relacionadas não são todas as dotações de uma política.',
            'Toda hipótese de exclusão digital exige revisão humana e, quando cabível, testes e triangulação.',
        ],
    }


def main() -> None:
    resultado = analise_principal()
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    conteudo = json.dumps(resultado, ensure_ascii=False, indent=2) + '\n'
    SAIDA.write_text(conteudo, encoding='utf-8')
    print('RADAR SP: triagem automática local concluída; SEM API de IA.')
    print('Atos:', resultado['resumo']['atos_historicos_catalogados'])
    print('Fichas de IA anteriores:', resultado['resumo']['fichas_ia_anteriores_verificadas'])
    print('Candidatos orçamentários:', resultado['resumo']['registros_de_orcamento_candidatos'])
    print('Pendências de integração:', resultado['resumo']['atos_adicionais_a_importar'])
    print('Saída:', SAIDA)


if __name__ == '__main__':
    main()
