import os
import subprocess
import asyncio
import re
import pandas as pd
import streamlit as st
import io
from playwright.async_api import async_playwright

# --- INSTALAÇÃO ---
def preparar_navegador():
    if 'navegador_pronto' not in st.session_state:
        try:
            subprocess.run(["playwright", "install", "chromium"], check=True)
            st.session_state['navegador_pronto'] = True
        except: pass

preparar_navegador()

# --- INTERFACE ---
st.set_page_config(page_title="Lead Miner Pro", page_icon="🎯", layout="wide")
st.title("🎯 Lead Miner Pro - Versão Global")

with st.sidebar:
    st.header("⚙️ Configurações")
    termos_raw = st.text_area("Termos", "Ortopedista\nDentista")
    cidades_raw = st.text_area("Locais (Bairros)", "Moema Sao Paulo\nTatuape Sao Paulo")
    excluir_raw = st.text_area("Palavras para Excluir", "hospital\npublico")
    concorrencia = st.slider("Buscas Simultâneas", 1, 3, 1) # Reduzi para 1 para evitar bloqueio
    max_rolagens = st.number_input("Rolagens", 5, 100, 30)

TERMOS = [t.strip() for t in termos_raw.split('\n') if t.strip()]
CIDADES = [c.strip() for c in cidades_raw.split('\n') if c.strip()]
PALAVRAS_EXCLUIR = [p.strip().lower() for p in excluir_raw.split('\n') if p.strip()]

# --- MOTOR ---
PADRAO_TEL = re.compile(r'\(?\d{2}\)?\s?9?\d{4,5}[\s-]?\d{4}')

async def extrair_contato(card, page):
    try:
        texto = await card.inner_text()
        tels = PADRAO_TEL.findall(texto)
        if tels: return tels[0]
        
        await card.click()
        await asyncio.sleep(2) # Mais tempo para carregar detalhes
        painel = await page.query_selector('[role="main"]')
        if painel:
            texto_painel = await painel.inner_text()
            tels = PADRAO_TEL.findall(texto_painel)
            if tels: return tels[0]
    except: pass
    return "Não listado"

async def executar_busca(context, setor, cidade, vistos, todos_leads, lock, status):
    busca = f"{setor} em {cidade}"
    status.write(f"🔎 Minerando: **{busca}**...")
    page = await context.new_page()
    
    try:
        # URL DIRETA E ROBUSTA
        url = f"https://www.google.com/maps/search/{busca.replace(' ', '+')}"
        await page.goto(url, wait_until="networkidle", timeout=60000)
        
        # Tenta aceitar cookies/termos se aparecerem (comum em servidores)
        try:
            for btn in await page.query_selector_all("button"):
                txt = await btn.inner_text()
                if "Aceitar" in txt or "Agree" in txt:
                    await btn.click()
        except: pass

        # Espera a lista de resultados carregar
        await asyncio.sleep(5) 
        
        # Lógica de Scroll
        for _ in range(max_rolagens):
            # Tenta rolar qualquer área que pareça uma lista de resultados
            await page.mouse.wheel(0, 2000)
            await asyncio.sleep(1)
            
        # Pega todos os cards de estabelecimentos
        cards = await page.query_selector_all('div[role="article"], a[href*="/maps/place/"]')
        
        for card in cards:
            try:
                nome = await card.get_attribute("aria-label")
                if not nome:
                    # Tenta pegar o nome de dentro do texto do card
                    nome = await card.inner_text()
                    nome = nome.split('\n')[0]

                if not nome or any(p in nome.lower() for p in PALAVRAS_EXCLUIR):
                    continue
                
                tel_bruto = await extrair_contato(card, page)
                tel_limpo = re.sub(r'\D', '', tel_bruto)
                
                async with lock:
                    chave = ("tel", tel_limpo) if len(tel_limpo) >= 10 else ("nome", nome.lower())
                    if chave not in vistos:
                        vistos.add(chave)
                        todos_leads.append({
                            "Nome": nome[:50], # Limita tamanho do nome
                            "Telefone_API": f"55{tel_limpo}" if len(tel_limpo) >= 10 else "Incompleto",
                            "Setor": setor,
                            "Cidade": cidade
                        })
            except: continue
    finally:
        await page.close()

async def engine():
    vistos = set()
    todos_leads = []
    lock = asyncio.Lock()
    semaforo = asyncio.Semaphore(concorrencia)
    log_area = st.empty()
    progresso = st.progress(0)
    buscas = [(s, c) for s in TERMOS for c in CIDADES]
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Contexto com cara de usuário real
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        for i, (setor, cidade) in enumerate(buscas):
            async with semaforo:
                await executar_busca(context, setor, cidade, vistos, todos_leads, lock, log_area)
                progresso.progress((i + 1) / len(buscas))
        
        await browser.close()
    return todos_leads

if st.button("🚀 Iniciar Mineração"):
    with st.spinner("Extraindo..."):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            res = loop.run_until_complete(engine())
            if res:
                df = pd.DataFrame(res)
                st.success(f"Encontrados {len(df)} leads!")
                st.dataframe(df)
                st.download_button("📥 Baixar CSV", df.to_csv(index=False, encoding='utf-8-sig'), "leads.csv")
            else:
                st.warning("Ainda sem resultados. O Google pode estar bloqueando o IP do servidor. Tente novamente em instantes ou mude o bairro.")
        except Exception as e:
            st.error(f"Erro: {e}")
