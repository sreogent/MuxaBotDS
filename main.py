import discord
from discord.ext import commands
import asyncio
import os

# Минимальная конфигурация
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, reconnect=True)

@bot.event
async def on_ready():
    print('🎉 БОТ УСПЕШНО ПОДКЛЮЧЕН!')
    print(f'🤖 Имя бота: {bot.user}')
    print(f'🆔 ID бота: {bot.user.id}')
    print(f'📊 Серверов: {len(bot.guilds)}')
    
    try:
        synced = await bot.tree.sync()
        print(f'✅ Синхронизировано {len(synced)} команд')
    except Exception as e:
        print(f'⚠️ Ошибка синхронизации команд: {e}')

@bot.tree.command(name="ping", description="Проверить работу бота")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f'🏓 Понг! {round(bot.latency * 1000)}мс')

@bot.tree.command(name="инфо", description="Информация о боте")
async def info(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 Информация о боте", color=discord.Color.blue())
    embed.add_field(name="Серверов", value=len(bot.guilds), inline=True)
    embed.add_field(name="Пинг", value=f"{round(bot.latency * 1000)}мс", inline=True)
    embed.add_field(name="Команды", value=len(bot.tree.get_commands()), inline=True)
    await interaction.response.send_message(embed=embed)

# Обработчики ошибок
@bot.event
async def on_error(event, *args, **kwargs):
    print(f'❌ Ошибка в событии {event}')

@bot.event
async def on_command_error(ctx, error):
    print(f'❌ Ошибка команды: {error}')

async def main():
    try:
        # Попытка подключения с таймаутом
        print('🔄 Попытка подключения к Discord...')
        async with asyncio.timeout(30):
            await bot.start("MTMzMzM1MDY4NTQxMjAzNjYzOA.G_qKSB.rZ6EuRxg3Tc_EjmI6nTNeS1fBz4Q1lwr3xAdPc")
    except asyncio.TimeoutError:
        print('❌ Таймаут подключения (30 сек)')
    except Exception as e:
        print(f'❌ Ошибка подключения: {e}')

if __name__ == "__main__":
    print('🚀 Запуск бота...')
    asyncio.run(main())
