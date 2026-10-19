// Frontend da loja: conversa com o API Gateway via REST (fetch) e recebe
// o status dos pedidos em tempo real via SSE (EventSource).

const API = "http://localhost:5000";

// Identificador do cliente, guardado no navegador para reencontrar os pedidos após recarregar.
const clienteId = obterClienteId();

const estado = {
  produtos: [],
  catalogo: new Map(), // produto_id -> produto (cache para o carrinho, mesmo com filtro ativo)
  categoria: "",
  carrinho: new Map(), // produto_id -> quantidade
  pedidos: new Map(),  // pedido_id -> pedido (com _links)
  abasPagamento: new Map(), // pedido_id -> aba aberta aguardando a URL do checkout
};

const STATUS_CLASSE = {
  "Pedido criado": "neutro",
  "Estoque confirmado": "info",
  "Aguardando pagamento": "alerta",
  "Pagamento aprovado": "ok",
  "Enviado": "ok",
  "Estoque indisponível": "erro",
  "Pagamento recusado": "erro",
  "Cancelado": "erro",
};

// ------------------------------------------------------------------ utilidades

function obterClienteId() {
  let id = null;
  try { id = localStorage.getItem("cliente_id"); } catch (e) { /* navegação privada */ }
  if (!id) {
    id = "cli-" + Math.random().toString(36).slice(2, 8);
    try { localStorage.setItem("cliente_id", id); } catch (e) { /* ignora */ }
  }
  return id;
}

const moeda = (v) => Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

function esc(texto) {
  const div = document.createElement("div");
  div.textContent = String(texto ?? "");
  return div.innerHTML;
}

function toast(mensagem, tipo = "") {
  const el = document.createElement("div");
  el.className = `toast ${tipo}`;
  el.textContent = mensagem;
  document.getElementById("toasts").appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

// Faz a requisição REST e devolve {status, corpo}; lança erro com a mensagem da API em caso de falha.
async function api(metodo, url, corpo) {
  const opcoes = { method: metodo, headers: {} };
  if (corpo !== undefined) {
    opcoes.headers["Content-Type"] = "application/json";
    opcoes.body = JSON.stringify(corpo);
  }
  const resposta = await fetch(url, opcoes);
  const texto = await resposta.text();
  const dados = texto ? JSON.parse(texto) : null;
  if (!resposta.ok) throw new Error(dados?.erro || `HTTP ${resposta.status}`);
  return { status: resposta.status, corpo: dados };
}

// ------------------------------------------------------------------ catálogo

async function carregarProdutos() {
  const url = new URL(`${API}/produtos`);
  if (estado.categoria) url.searchParams.set("categoria", estado.categoria);
  try {
    const { corpo } = await api("GET", url);
    estado.produtos = corpo;
    corpo.forEach((p) => estado.catalogo.set(p.id, p));
    renderProdutos();
    renderCarrinho();
  } catch (e) {
    document.getElementById("produtos").innerHTML = `<p class="vazio">Não foi possível carregar os produtos (${esc(e.message)}).</p>`;
  }
}

function renderProdutos() {
  const el = document.getElementById("produtos");
  if (!estado.produtos.length) {
    el.innerHTML = `<p class="vazio">Nenhum produto disponível.</p>`;
    return;
  }
  el.innerHTML = estado.produtos.map((p) => `
    <article class="produto">
      <span class="categoria">Categoria ${esc(p.categoria)}</span>
      <h4>${esc(p.nome)}</h4>
      <span class="preco">${moeda(p.preco)}</span>
      <span class="estoque">${p.quantidade} em estoque</span>
      <div class="acoes">
        <input type="number" min="1" max="${p.quantidade}" value="1" id="qtd-${p.id}" aria-label="Quantidade">
        <button class="botao primario" data-adicionar="${p.id}">Adicionar</button>
      </div>
    </article>`).join("");
}

// ------------------------------------------------------------------ carrinho

function adicionarAoCarrinho(produtoId) {
  const qtd = parseInt(document.getElementById(`qtd-${produtoId}`).value, 10);
  if (!qtd || qtd <= 0) return toast("Quantidade inválida", "erro");
  estado.carrinho.set(produtoId, (estado.carrinho.get(produtoId) || 0) + qtd);
  renderCarrinho();
}

function renderCarrinho() {
  const lista = document.getElementById("carrinho");
  let total = 0;
  const linhas = [];
  for (const [id, qtd] of estado.carrinho) {
    const p = estado.catalogo.get(id);
    const nome = p ? p.nome : `Produto ${id}`;
    if (p) total += p.preco * qtd;
    linhas.push(`<li><span>${qtd}× ${esc(nome)}</span><span>${p ? moeda(p.preco * qtd) : ""}
      <button title="Remover" data-remover="${id}">✕</button></span></li>`);
  }
  lista.innerHTML = linhas.join("") || `<li class="vazio">Carrinho vazio</li>`;
  document.getElementById("carrinho-total").textContent = moeda(total);
  document.getElementById("finalizar").disabled = estado.carrinho.size === 0;
}

// POST /pedidos. A aba de pagamento é aberta já no clique (gesto do usuário, para não ser
// bloqueada como pop-up) e recebe a URL do checkout assim que ela chegar pelo SSE.
async function finalizarPedido() {
  const itens = [...estado.carrinho].map(([produto_id, quantidade]) => ({ produto_id, quantidade }));
  const aba = window.open("", "_blank");
  if (aba) {
    aba.document.write(`<title>Aguardando pagamento…</title><body style="font-family:system-ui;display:grid;place-items:center;height:90vh;color:#555">
      <p>Reservando o estoque e gerando a cobrança…</p></body>`);
  }
  try {
    const { corpo: pedido } = await api("POST", `${API}/pedidos`, { cliente_id: clienteId, itens });
    estado.pedidos.set(pedido.id, pedido);
    if (aba) estado.abasPagamento.set(pedido.id, aba);
    estado.carrinho.clear();
    renderCarrinho();
    renderPedidos(pedido.id);
    toast(`Pedido #${pedido.id} criado`);
  } catch (e) {
    if (aba) aba.close();
    toast(`Erro ao criar pedido: ${e.message}`, "erro");
  }
}

// ------------------------------------------------------------------ pedidos

async function carregarPedidos() {
  try {
    const { corpo } = await api("GET", `${API}/pedidos?cliente_id=${encodeURIComponent(clienteId)}`);
    estado.pedidos = new Map(corpo.map((p) => [p.id, p]));
    renderPedidos();
  } catch (e) {
    toast(`Não foi possível carregar os pedidos: ${e.message}`, "erro");
  }
}

function renderPedidos(destacar) {
  const el = document.getElementById("pedidos");
  const lista = [...estado.pedidos.values()].sort((a, b) => b.id - a.id);
  if (!lista.length) {
    el.innerHTML = `<p class="vazio">Nenhum pedido ainda.</p>`;
    return;
  }
  el.innerHTML = lista.map((p) => {
    const links = p._links || {};
    const itens = p.itens.map((i) => `${i.quantidade}× ${esc(i.nome)}`).join(", ");
    // HATEOAS: os botões só aparecem se o servidor enviou o link da ação.
    const acoes = [
      links.pagamento ? `<a class="botao sucesso" href="${esc(links.pagamento.href)}" target="_blank">Pagar</a>` : "",
      links.cancelar ? `<button class="botao perigo" data-cancelar="${p.id}">Cancelar pedido</button>` : "",
    ].join("");
    const historico = p.historico.map((h) =>
      `<li><b>${esc(h.status)}</b> · ${esc(h.em.slice(11))} — ${esc(h.detalhe)}</li>`).join("");
    return `
      <article class="pedido ${p.id === destacar ? "destaque" : ""}">
        <div class="pedido-topo">
          <h4>Pedido #${p.id} · ${moeda(p.total)}</h4>
          <span class="badge ${STATUS_CLASSE[p.status] || "neutro"}">${esc(p.status)}</span>
        </div>
        <div class="itens">${itens}</div>
        <div class="detalhe">${esc(p.detalhe)}</div>
        ${acoes ? `<div class="acoes">${acoes}</div>` : ""}
        <details><summary class="ajuda">Histórico</summary><ul class="linha-tempo">${historico}</ul></details>
      </article>`;
  }).join("");
}

async function cancelarPedido(id) {
  const link = estado.pedidos.get(id)?._links?.cancelar;
  if (!link) return;
  try {
    await api(link.method, link.href);
    toast(`Pedido #${id} cancelado`);
  } catch (e) {
    toast(`Não foi possível cancelar: ${e.message}`, "erro");
  }
}

// ------------------------------------------------------------------ SSE

function conectarSSE() {
  const indicador = document.getElementById("sse-status");
  const fonte = new EventSource(`${API}/eventos?cliente_id=${encodeURIComponent(clienteId)}`);

  fonte.addEventListener("conectado", () => {
    indicador.className = "sse on";
    indicador.textContent = "SSE conectado";
  });

  // Cada evento "pedido" traz a representação completa e atualizada do pedido.
  fonte.addEventListener("pedido", (e) => {
    const pedido = JSON.parse(e.data);
    estado.pedidos.set(pedido.id, pedido);
    renderPedidos(pedido.id);
    tratarAbaPagamento(pedido);

    const tipo = STATUS_CLASSE[pedido.status] === "erro" ? "erro" : STATUS_CLASSE[pedido.status] === "ok" ? "ok" : "";
    toast(`Pedido #${pedido.id}: ${pedido.status}`, tipo);
    if (["Estoque confirmado", "Cancelado", "Estoque indisponível", "Pagamento recusado"].includes(pedido.status)) {
      carregarProdutos(); // estoque mudou (reserva ou devolução)
    }
  });

  // O EventSource reconecta sozinho; aqui só atualizamos o indicador.
  fonte.onerror = () => {
    indicador.className = "sse off";
    indicador.textContent = "SSE reconectando…";
  };
}

// Leva a aba aberta no "Finalizar pedido" para o checkout do Mock de Pagamento.
function tratarAbaPagamento(pedido) {
  const aba = estado.abasPagamento.get(pedido.id);
  if (!aba) return;
  if (aba.closed) {
    estado.abasPagamento.delete(pedido.id);
  } else if (pedido._links?.pagamento) {
    aba.location.href = pedido._links.pagamento.href;
    estado.abasPagamento.delete(pedido.id);
  } else if (["Estoque indisponível", "Pagamento recusado", "Cancelado"].includes(pedido.status)) {
    aba.close();
    estado.abasPagamento.delete(pedido.id);
  }
}

// ------------------------------------------------------------------ promoções

function categoriasMarcadas() {
  return [...document.querySelectorAll("#form-interesse input[type=checkbox]:checked")].map((c) => c.value);
}

async function inscrever(evento) {
  evento.preventDefault();
  const email = document.getElementById("email").value.trim();
  const categorias = categoriasMarcadas();
  if (!categorias.length) return toast("Marque ao menos uma categoria", "erro");
  try {
    await api("POST", `${API}/interesses`, { email, categorias });
    toast(`Inscrição enviada: ${categorias.join(", ")}`, "ok");
  } catch (e) {
    toast(`Erro: ${e.message}`, "erro");
  }
}

async function cancelarInteresse() {
  const email = document.getElementById("email").value.trim();
  if (!email) return toast("Informe o e-mail", "erro");
  const categorias = categoriasMarcadas();
  const base = `${API}/interesses/${encodeURIComponent(email)}`;
  try {
    if (categorias.length) {
      for (const c of categorias) await api("DELETE", `${base}/categorias/${c}`);
    } else {
      await api("DELETE", base);
    }
    toast(`Interesse cancelado: ${categorias.join(", ") || "todas as categorias"}`);
  } catch (e) {
    toast(`Erro: ${e.message}`, "erro");
  }
}

// ------------------------------------------------------------------ eventos da página

document.getElementById("cliente-id").textContent = clienteId;

document.getElementById("produtos").addEventListener("click", (e) => {
  const id = e.target.dataset.adicionar;
  if (id) adicionarAoCarrinho(Number(id));
});
document.getElementById("carrinho").addEventListener("click", (e) => {
  const id = e.target.dataset.remover;
  if (id) { estado.carrinho.delete(Number(id)); renderCarrinho(); }
});
document.getElementById("pedidos").addEventListener("click", (e) => {
  const id = e.target.dataset.cancelar;
  if (id) cancelarPedido(Number(id));
});
document.getElementById("filtros").addEventListener("click", (e) => {
  if (!e.target.dataset || e.target.dataset.categoria === undefined) return;
  estado.categoria = e.target.dataset.categoria;
  document.querySelectorAll("#filtros .chip").forEach((c) => c.classList.toggle("ativo", c === e.target));
  carregarProdutos();
});
document.getElementById("finalizar").addEventListener("click", finalizarPedido);
document.getElementById("form-interesse").addEventListener("submit", inscrever);
document.getElementById("cancelar-interesse").addEventListener("click", cancelarInteresse);

renderCarrinho();
carregarProdutos();
carregarPedidos();
conectarSSE();
