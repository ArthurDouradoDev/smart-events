/**
 * units.js — Escala e formatação das unidades canônicas dos KPIs.
 *
 * Depois da Fase 3 todo valor chega ao frontend na unidade-base declarada pelo
 * catálogo (`bit`, `bit/s`, `%`, `dBm`, `ms`, `usuários`), o que deixou os
 * números de volume e throughput com 7 a 9 dígitos. Aqui eles voltam a ser
 * legíveis sem que ninguém precise reimplementar o cálculo em cada tela: é este
 * o único formatador do app.
 *
 * Duas decisões que não são estéticas:
 *
 * 1. **A escala é decimal, não binária.** `kbit`/`Mbit`/`Gbit` são potências de
 *    10 por definição em telecom — o `KiB`/`MiB` de sistema de arquivos não vale
 *    aqui e produziria 2,4% de erro por degrau.
 * 2. **O degrau é memorizado por métrica, não recalculado por painel.** Medido
 *    no evento real: dois sites no mesmo instante tinham 3,3e7 e 2,5e9 bit; com
 *    escala independente sairiam "33,5 M" e "2,49 G", e 33,5 *parece* maior que
 *    2,49. Além disso, na janela deslizante de 15 min o degrau de um dos sites
 *    trocaria sozinho 17 vezes em 427 pontos. A histerese (sobe em 1000, desce
 *    só abaixo de 900) fecha o resto da oscilação.
 */

const DEGRAUS = {
  "bit":   ["bit", "kbit", "Mbit", "Gbit", "Tbit"],
  "bit/s": ["bit/s", "kbit/s", "Mbit/s", "Gbit/s", "Tbit/s"],
};

/**
 * Só grandeza de dados escala; percentual, dBm, ms e contagem passam intactos.
 * Derivado de DEGRAUS de propósito: duas listas separadas divergiriam.
 */
export const ESCALAVEIS = new Set(Object.keys(DEGRAUS));

const PASSO = 1000;
const SOBE_EM = 1000;   // >= 1000 no degrau atual sobe
const DESCE_EM = 900;   // < 900 do degrau de baixo desce (histerese)

/** Escala neutra: usada por tudo que não é `bit`/`bit/s`. */
function _semEscala(unidadeBase) {
  return { divisor: 1, rotulo: unidadeBase || "", degrau: 0, unidadeBase: unidadeBase || "" };
}

/** Maior valor absoluto finito de uma lista (aceita listas aninhadas). */
function _maiorAbsoluto(valores) {
  let maximo = null;
  const visitar = item => {
    if (Array.isArray(item)) { item.forEach(visitar); return; }
    // Buraco de coleta não é zero: `Number(null)` é 0 e faria uma série toda
    // vazia derrubar o degrau para `bit` — a unidade do cabeçalho piscaria.
    if (item == null || item === "") return;
    const numero = Number(item);
    if (!Number.isFinite(numero)) return;
    const absoluto = Math.abs(numero);
    if (maximo == null || absoluto > maximo) maximo = absoluto;
  };
  visitar(valores);
  return maximo;
}

function _degrauPara(maximo, degrauAtual, total) {
  let degrau = Math.min(Math.max(degrauAtual ?? 0, 0), total - 1);
  while (degrau < total - 1 && maximo >= SOBE_EM * PASSO ** degrau) degrau += 1;
  while (degrau > 0 && maximo < DESCE_EM * PASSO ** (degrau - 1)) degrau -= 1;
  return degrau;
}

/**
 * Escolhe o degrau de escala para um conjunto de valores.
 *
 * @param {Array} valores      valores na unidade-base (aceita aninhamento e nulos)
 * @param {string} unidadeBase unidade canônica vinda do catálogo
 * @param {number|null} degrauAtual degrau vigente, quando existe — é ele que
 *        ativa a histerese; sem ele a escolha é direta pelo maior valor.
 * @returns {{divisor: number, rotulo: string, degrau: number, unidadeBase: string}}
 */
export function escolherEscala(valores, unidadeBase, degrauAtual = null) {
  const degraus = DEGRAUS[unidadeBase];
  if (!degraus) return _semEscala(unidadeBase);
  const maximo = _maiorAbsoluto(valores);
  // Painel sem nenhum ponto não é motivo para trocar de degrau: mantém o que
  // estava, senão a unidade do cabeçalho piscaria a cada buraco de coleta.
  const degrau = maximo == null
    ? Math.min(Math.max(degrauAtual ?? 0, 0), degraus.length - 1)
    : _degrauPara(maximo, degrauAtual, degraus.length);
  return { divisor: PASSO ** degrau, rotulo: degraus[degrau], degrau, unidadeBase };
}

/**
 * Formata um valor na escala escolhida, em pt-BR.
 *
 * @param {number} valor  valor na unidade-base
 * @param {object|null} escala  resultado de `escolherEscala`; ausente = sem divisão
 * @param {{minimoDeCasas?: number, maximoDeCasas?: number}} opcoes
 */
export function formatar(valor, escala = null, opcoes = {}) {
  const numero = Number(valor);
  if (valor == null || !Number.isFinite(numero)) return "—";
  const minimo = opcoes.minimoDeCasas ?? 0;
  const maximo = Math.max(opcoes.maximoDeCasas ?? 2, minimo);
  return (numero / (escala?.divisor || 1)).toLocaleString("pt-BR", {
    minimumFractionDigits: minimo,
    maximumFractionDigits: maximo,
  });
}

// ── Memória de escala ──────────────────────────────────────────────
//
// Uma entrada por chave de exibição (métrica, ou painel pareado que divide o
// eixo). Vive enquanto a página vive; troca de evento a zera.

const _memoria = new Map();

/**
 * Escala vigente de uma métrica, atualizada com os valores recém-carregados.
 * É por aqui que a histerese age: o degrau anterior entra como referência.
 */
export function escalaDaMetrica(chave, valores, unidadeBase) {
  const anterior = _memoria.get(chave);
  const degrauAtual = anterior?.unidadeBase === unidadeBase ? anterior.degrau : null;
  const escala = escolherEscala(valores, unidadeBase, degrauAtual);
  _memoria.set(chave, escala);
  return escala;
}

/** Zera a memória — chamado na troca de evento, onde a grandeza muda de vez. */
export function esquecerEscalas() {
  _memoria.clear();
}
