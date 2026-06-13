import discord
from discord.ext import commands
from discord.ui import Button, View
from flask import Flask
from threading import Thread
import yt_dlp
import asyncio
import os
from collections import deque

# ========== CONFIGURAÇÕES ==========
TOKEN = os.getenv("TOKEN")
if TOKEN is None:
    TOKEN = ""
PREFIXO = "!"

# ========== SERVIDOR WEB ==========
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot online!"

def run():
    app.run(host='0.0.0.0', port=8080)

Thread(target=run).start()

# ========== BOT ==========
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=PREFIXO, intents=intents)

# ========== VARIÁVEIS GLOBAIS ==========
fila_de_musicas = deque()
musica_atual = None
modo_loop = False
volume_do_bot = 0.5

# ========== CONFIGURAÇÃO YT-DLP ==========
YDL_OPTS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
}

# ========== FUNÇÕES ==========
def buscar_musica(busca):
    """Busca música no SoundCloud ou converte Spotify"""
    
    if 'spotify.com' in busca:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(busca, download=False)
            artista = info.get('artist', '')
            titulo = info.get('title', '')
            busca = f"{artista} {titulo}"
    
    if not busca.startswith('http'):
        busca = f"scsearch:{busca}"
    
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(busca, download=False)
        if 'entries' in info and len(info['entries']) > 0:
            info = info['entries'][0]
        
        url = info.get('url', '')
        
        return {
            'url': url,
            'titulo': info.get('title', 'Desconhecido'),
            'artista': info.get('uploader', 'Desconhecido'),
            'thumb': info.get('thumbnail', ''),
        }


async def tocar_fila(vc):
    """Toca a próxima música da fila"""
    global musica_atual, modo_loop
    
    if not vc or not vc.is_connected():
        return
    
    # Loop da música atual
    if modo_loop and musica_atual:
        info = musica_atual
        try:
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(info['url'], before_options="-reconnect 1 -reconnect_streamed 1"),
                volume=volume_do_bot
            )
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop))
        except:
            musica_atual = None
            asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop)
        return
    
    # Toca próxima da fila
    while fila_de_musicas:
        info = fila_de_musicas.popleft()
        if not info.get('url'):
            continue
        
        try:
            musica_atual = info
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(info['url'], before_options="-reconnect 1 -reconnect_streamed 1"),
                volume=volume_do_bot
            )
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop))
            return
        except:
            musica_atual = None
            continue
    
    musica_atual = None


# ========== PAINEL COM BOTÕES ==========
class Painel(View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="⏯️ Pause", style=discord.ButtonStyle.green, custom_id="b_pause")
    async def b_pause(self, i: discord.Interaction, b: Button):
        vc = i.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await i.response.send_message("⏸️ Pausado", ephemeral=True)
        elif vc and vc.is_paused():
            vc.resume()
            await i.response.send_message("▶️ Tocando", ephemeral=True)
        else:
            await i.response.send_message("❌ Nada tocando", ephemeral=True)
    
    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.gray, custom_id="b_skip")
    async def b_skip(self, i: discord.Interaction, b: Button):
        vc = i.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await i.response.send_message("⏭️ Pulando", ephemeral=True)
        else:
            await i.response.send_message("❌ Nada tocando", ephemeral=True)
    
    @discord.ui.button(label="🔁 Loop", style=discord.ButtonStyle.blurple, custom_id="b_loop")
    async def b_loop(self, i: discord.Interaction, b: Button):
        global modo_loop
        modo_loop = not modo_loop
        estado = "ON 🔁" if modo_loop else "OFF"
        await i.response.send_message(f"Loop: **{estado}**", ephemeral=True)
    
    @discord.ui.button(label="🛑 Parar", style=discord.ButtonStyle.red, custom_id="b_stop")
    async def b_stop(self, i: discord.Interaction, b: Button):
        global fila_de_musicas, modo_loop
        vc = i.guild.voice_client
        if vc:
            fila_de_musicas.clear()
            modo_loop = False
            vc.stop()
            await vc.disconnect()
            await i.response.send_message("🛑 Parou", ephemeral=True)
        else:
            await i.response.send_message("❌ Não conectado", ephemeral=True)


# ========== COMANDOS ==========
@bot.command()
async def painel(ctx):
    """Abre o painel de controle"""
    embed = discord.Embed(title="🎵 Painel de Música", color=0x8A2BE2)
    if musica_atual:
        embed.add_field(name="🎧 Tocando", value=f"**{musica_atual['titulo'][:100]}**\n👤 {musica_atual['artista'][:50]}", inline=False)
        if musica_atual['thumb']:
            embed.set_thumbnail(url=musica_atual['thumb'])
    else:
        embed.add_field(name="🎧 Tocando", value="Nada", inline=False)
    
    if fila_de_musicas:
        txt = ""
        for i, m in enumerate(fila_de_musicas, 1):
            txt += f"`{i}.` {m['titulo'][:50]}\n"
        embed.add_field(name="📋 Fila", value=txt[:1024], inline=False)
    else:
        embed.add_field(name="📋 Fila", value="Vazia", inline=False)
    
    embed.add_field(name="🔁 Loop", value="ON" if modo_loop else "OFF")
    embed.add_field(name="🔊 Volume", value=f"{int(volume_do_bot * 100)}%")
    embed.set_footer(text="!comandos | SoundCloud + Spotify")
    await ctx.send(embed=embed, view=Painel())


@bot.command()
async def play(ctx, *, busca=None):
    """Toca música"""
    if busca is None:
        await ctx.send("❌ Use: `!play nome_da_música` ou `!play link`")
        return
    if not ctx.author.voice:
        await ctx.send("❌ Você precisa estar em um canal de voz!")
        return
    
    canal = ctx.author.voice.channel
    
    if ctx.voice_client is None:
        vc = await canal.connect()
    else:
        vc = ctx.voice_client
        if vc.channel != canal:
            await vc.move_to(canal)
    
    await ctx.send(f"🔎 Procurando: `{busca}`...")
    
    try:
        info = await asyncio.to_thread(buscar_musica, busca)
    except Exception as e:
        await ctx.send("❌ Não encontrei. Tente outro nome ou link.")
        return
    
    if not info.get('url'):
        await ctx.send("❌ Esta música não pode ser tocada. Tente outra.")
        return
    
    if vc.is_playing() or vc.is_paused():
        fila_de_musicas.append(info)
        embed = discord.Embed(title="📋 Adicionado à fila", description=f"**{info['titulo'][:100]}**", color=0xFF5500)
        if info['thumb']:
            embed.set_thumbnail(url=info['thumb'])
        embed.add_field(name="👤 Artista", value=info['artista'][:50], inline=True)
        embed.add_field(name="📋 Posição", value=str(len(fila_de_musicas)), inline=True)
        await ctx.send(embed=embed)
    else:
        global musica_atual
        musica_atual = info
        try:
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(info['url'], before_options="-reconnect 1 -reconnect_streamed 1"),
                volume=volume_do_bot
            )
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop))
            
            embed = discord.Embed(title="🎧 Tocando agora", description=f"**{info['titulo'][:100]}**", color=0xFF5500)
            if info['thumb']:
                embed.set_thumbnail(url=info['thumb'])
            embed.add_field(name="👤 Artista", value=info['artista'][:50], inline=True)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ Erro ao tocar. Tente outra música.")
            musica_atual = None


@bot.command()
async def fila(ctx):
    """Mostra a fila"""
    if not fila_de_musicas:
        await ctx.send("📋 Fila vazia.")
        return
    
    embed = discord.Embed(title="📋 Fila de Músicas", color=0xFF5500)
    if musica_atual:
        embed.add_field(name="🎧 Tocando agora", value=f"**{musica_atual['titulo'][:80]}**", inline=False)
    
    txt = ""
    for i, m in enumerate(fila_de_musicas, 1):
        txt += f"`{i}.` {m['titulo'][:50]}\n"
    embed.add_field(name="📋 Próximas", value=txt[:1024] or "Nenhuma", inline=False)
    embed.set_footer(text=f"Total na fila: {len(fila_de_musicas)} música(s)")
    await ctx.send(embed=embed)


@bot.command()
async def tocando(ctx):
    """Mostra música atual"""
    if musica_atual:
        info = musica_atual
        embed = discord.Embed(title="🎧 Tocando agora", description=f"**{info['titulo'][:100]}**", color=0x8A2BE2)
        if info['thumb']:
            embed.set_thumbnail(url=info['thumb'])
        embed.add_field(name="👤 Artista", value=info['artista'][:50])
        embed.add_field(name="🔁 Loop", value="ON" if modo_loop else "OFF")
        embed.add_field(name="🔊 Volume", value=f"{int(volume_do_bot * 100)}%")
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ Nada tocando.")


@bot.command()
async def skip(ctx):
    """Pula a música"""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Pulando...")
    else:
        await ctx.send("❌ Nada tocando.")


@bot.command()
async def pause(ctx):
    """Pausa"""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Pausado!")
    else:
        await ctx.send("❌ Nada tocando.")


@bot.command()
async def continuar(ctx):
    """Continua"""
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Tocando!")
    else:
        await ctx.send("❌ Não está pausado.")


@bot.command()
async def loop(ctx):
    """Ativa/desativa loop"""
    global modo_loop
    modo_loop = not modo_loop
    estado = "ativado 🔁" if modo_loop else "desativado"
    await ctx.send(f"Loop **{estado}**.")


@bot.command()
async def volume(ctx, vol: int = None):
    """Ajusta volume 0-100"""
    global volume_do_bot
    if vol is None:
        await ctx.send(f"🔊 Volume atual: **{int(volume_do_bot * 100)}%**")
        return
    if vol < 0 or vol > 100:
        await ctx.send("❌ Digite um valor entre 0 e 100.")
        return
    volume_do_bot = vol / 100
    if ctx.voice_client and ctx.voice_client.source:
        if isinstance(ctx.voice_client.source, discord.PCMVolumeTransformer):
            ctx.voice_client.source.volume = volume_do_bot
    await ctx.send(f"🔊 Volume: **{vol}%**")


@bot.command()
async def remover(ctx, posicao: int = None):
    """Remove música da fila"""
    global fila_de_musicas
    if posicao is None or posicao < 1 or posicao > len(fila_de_musicas):
        await ctx.send("❌ Use: `!remover <número>`")
        return
    lista = list(fila_de_musicas)
    removida = lista.pop(posicao - 1)
    fila_de_musicas = deque(lista)
    await ctx.send(f"🗑️ Removido: **{removida['titulo'][:50]}**")


@bot.command()
async def limpar(ctx):
    """Limpa a fila"""
    global fila_de_musicas
    fila_de_musicas.clear()
    await ctx.send("🧹 Fila limpa!")


@bot.command()
async def parar(ctx):
    """Para tudo e sai"""
    global fila_de_musicas, modo_loop, musica_atual
    fila_de_musicas.clear()
    modo_loop = False
    musica_atual = None
    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.voice_client.disconnect()
        await ctx.send("⏹️ Parei e saí do canal. Até mais!")
    else:
        await ctx.send("❌ Não estou em nenhum canal.")


@bot.command()
async def comandos(ctx):
    """Mostra todos os comandos"""
    embed = discord.Embed(title="🎵 Comandos do Dante", description="Prefixo: `!` | SoundCloud + Spotify", color=0x8A2BE2)
    embed.add_field(name="🎧 Música", value="`!play nome/link` - Toca música\n`!pause` - Pausa\n`!continuar` - Continua\n`!skip` - Pula\n`!tocando` - Música atual", inline=False)
    embed.add_field(name="📋 Fila", value="`!fila` - Ver fila\n`!remover <n>` - Remove música\n`!limpar` - Limpa fila", inline=False)
    embed.add_field(name="⚙️ Controle", value="`!painel` - Painel com botões\n`!loop` - Repetir música\n`!volume <0-100>` - Volume\n`!parar` - Para tudo", inline=False)
    embed.add_field(name="🔗 Plataformas", value="✅ SoundCloud (busca por nome)\n✅ Spotify (cole o link)", inline=False)
    embed.set_footer(text="Dante 24/7 • Online sempre!")
    await ctx.send(embed=embed)


@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="!comandos | SoundCloud + Spotify"))
    print(f"✅ Bot {bot.user} está online!")

bot.run(TOKEN)
