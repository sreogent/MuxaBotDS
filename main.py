import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput, Select
import asyncio
from datetime import datetime, timedelta
import os

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

active_obzvons = {}
reports = {}

# Каналы для логирования
LOG_CHANNELS = {
    'forms': 'формы-наказаний',
    'messages': 'сообщения',
    'users': 'пользователи',
    'voice': 'голосовые-каналы',
    'reports': 'репорт',
    'private': 'приватные-комнаты',
    'calls': 'обзвоны',
    'auto_punish': 'авто-наказания',
    'moderators': 'модераторы',
    'economy': 'экономика'
}

# Хранилище настроек серверов
server_settings = {}

# Роли для выдачи (по умолчанию)
AVAILABLE_ROLES = [
    'Новичок', 'Участник', 'Активный', 'VIP', 'Модератор', 'Администратор'
]


class ReportCreateModal(Modal, title='Создать репорт'):
    description = TextInput(label='Описание проблемы',
                            style=discord.TextStyle.paragraph,
                            placeholder='Опишите детально что произошло...')

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        report_id = f'report-{int(datetime.utcnow().timestamp())}'

        embed = discord.Embed(title='🚨 Новый репорт',
                              description=self.description.value,
                              color=discord.Color.red(),
                              timestamp=datetime.utcnow())
        embed.add_field(name='Автор',
                        value=interaction.user.mention,
                        inline=True)
        embed.add_field(name='Канал', value=self.channel.mention, inline=True)
        embed.add_field(name='ID репорта', value=report_id, inline=False)

        # Отправляем в канал репортов
        report_channel = discord.utils.get(interaction.guild.text_channels,
                                           name='репорт')
        if report_channel:
            view = ReportActionView(report_id, interaction.user, self.channel)
            await report_channel.send(embed=embed, view=view)

        await interaction.response.send_message(
            '✅ Репорт отправлен модераторам!', ephemeral=True)


class ReportActionView(View):

    def __init__(self, report_id, author, channel):
        super().__init__(timeout=None)
        self.report_id = report_id
        self.author = author
        self.channel = channel

    @discord.ui.button(label='Принять',
                       style=discord.ButtonStyle.success,
                       emoji='✅')
    async def accept_report(self, interaction: discord.Interaction,
                            button: Button):
        embed = discord.Embed(
            title='✅ Репорт принят',
            description=
            f'Репорт {self.report_id} принят модератором {interaction.user.mention}',
            color=discord.Color.green())
        await interaction.response.edit_message(embed=embed, view=None)

        # Логируем в канал модераторов
        await log_action(
            'moderators', interaction.guild,
            f'🟢 Репорт {self.report_id} принят модератором {interaction.user.mention}'
        )

    @discord.ui.button(label='Отклонить',
                       style=discord.ButtonStyle.danger,
                       emoji='❌')
    async def decline_report(self, interaction: discord.Interaction,
                             button: Button):
        embed = discord.Embed(
            title='❌ Репорт отклонён',
            description=
            f'Репорт {self.report_id} отклонён модератором {interaction.user.mention}',
            color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=None)

        # Логируем в канал модераторов
        await log_action(
            'moderators', interaction.guild,
            f'🔴 Репорт {self.report_id} отклонён модератором {interaction.user.mention}'
        )


class RoleSelectView(View):

    def __init__(self, guild):
        super().__init__(timeout=30)
        self.add_item(RoleSelect(guild))


class RoleSelect(Select):

    def __init__(self, guild):
        # Получаем все роли сервера, кроме @everyone и ботовских
        server_roles = [
            role for role in guild.roles
            if role.name != '@everyone' and not role.managed
        ]

        # Ограничиваем до 25 ролей (лимит Discord Select)
        server_roles = server_roles[:25]

        options = [
            discord.SelectOption(label=role.name,
                                 value=str(role.id),
                                 description=f'Позиция {role.position}')
            for role in server_roles
        ]

        if not options:
            options = [
                discord.SelectOption(label='Нет доступных ролей', value='none')
            ]

        super().__init__(placeholder='Выберите роль для выдачи',
                         options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == 'none':
            await interaction.response.send_message(
                '❌ На сервере нет доступных ролей для выдачи.', ephemeral=True)
            return

        role_id = int(self.values[0])
        role = interaction.guild.get_role(role_id)

        if not role:
            await interaction.response.send_message('❌ Роль не найдена.',
                                                    ephemeral=True)
            return

        await interaction.response.send_message(
            f'Выберите пользователя для выдачи роли `{role.name}`',
            view=UserSelectView(role),
            ephemeral=True)


class UserSelectView(View):

    def __init__(self, role):
        super().__init__(timeout=30)
        self.role = role
        self.add_item(UserSelect(role))


class UserSelect(Select):

    def __init__(self, role):
        # Создаем простые опции для примера
        options = [
            discord.SelectOption(label='Ввести ID или упоминание',
                                 value='manual_input',
                                 description='Ввести пользователя вручную')
        ]
        super().__init__(placeholder='Выберите способ',
                         options=options,
                         min_values=1,
                         max_values=1)
        self.role = role

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(UserInputModal(self.role))


class UserInputModal(Modal, title='Выдача роли'):
    user_input = TextInput(label='ID или упоминание пользователя',
                           placeholder='123456789012345678 или @username')

    def __init__(self, role):
        super().__init__()
        self.role = role

    async def on_submit(self, interaction: discord.Interaction):
        user_str = self.user_input.value.strip()

        # Пытаемся найти пользователя
        member = None
        if user_str.startswith('<@') and user_str.endswith('>'):
            user_id = user_str[2:-1].replace('!', '')
            member = interaction.guild.get_member(int(user_id))
        elif user_str.isdigit():
            member = interaction.guild.get_member(int(user_str))
        else:
            member = discord.utils.get(interaction.guild.members,
                                       name=user_str)

        if not member:
            await interaction.response.send_message(
                '❌ Пользователь не найден.', ephemeral=True)
            return

        try:
            await member.add_roles(self.role)
            embed = discord.Embed(
                title='✅ Роль выдана',
                description=
                f'Роль `{self.role.name}` выдана пользователю {member.mention}',
                color=discord.Color.green())
            embed.add_field(name='Модератор', value=interaction.user.mention)
            await interaction.response.send_message(embed=embed)

            # Логируем выдачу роли
            await log_action(
                'moderators', interaction.guild,
                f'🎭 {interaction.user.mention} выдал роль `{self.role.name}` пользователю {member.mention}'
            )

        except Exception as e:
            await interaction.response.send_message(
                f'❌ Ошибка при выдаче роли: {str(e)}', ephemeral=True)


async def log_action(log_type, guild, message):
    """Функция для логирования действий"""
    if log_type in LOG_CHANNELS:
        channel_name = LOG_CHANNELS[log_type]
        channel = discord.utils.get(guild.text_channels, name=channel_name)

        # Если канал не найден, пытаемся создать его
        if not channel:
            try:
                # Создаем категорию для логов если её нет
                log_category = discord.utils.get(guild.categories,
                                                 name='📊 Логи')
                if not log_category:
                    log_category = await guild.create_category('📊 Логи')

                # Создаем канал логирования
                channel = await guild.create_text_channel(
                    channel_name,
                    category=log_category,
                    topic=f'Автоматическое логирование {log_type}')
                print(f'✅ Создан канал логирования {channel_name}')
            except Exception as e:
                print(f'❌ Не удалось создать канал {channel_name}: {e}')
                return

        if channel:
            try:
                embed = discord.Embed(description=message,
                                      color=discord.Color.blue(),
                                      timestamp=datetime.utcnow())
                embed.set_footer(text=f'Тип лога: {log_type}')
                await channel.send(embed=embed)
            except Exception as e:
                print(f'❌ Ошибка отправки лога в {channel_name}: {e}')


@bot.tree.command(name='репорт',
                  description='Создать репорт по текущему каналу')
async def create_report(interaction: discord.Interaction):
    await interaction.response.send_modal(
        ReportCreateModal(interaction.channel))


@bot.tree.command(name='роль', description='Выдать роль пользователю')
async def give_role(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message(
            '❌ У вас нет прав на выдачу ролей.', ephemeral=True)
        return

    await interaction.response.send_message('Выберите роль для выдачи',
                                            view=RoleSelectView(
                                                interaction.guild),
                                            ephemeral=True)


@bot.tree.command(name='создать_обзвон_бот',
                  description='Создать обзвон как BLACK CHANNEL BOT')
async def create_bot_call(interaction: discord.Interaction):
    embed = discord.Embed(
        title='Обзвон',
        description=
        'Вы можете создать специальную категорию со всеми необходимыми каналами и требуемым функционалом для удобного проведения обзвонов.',
        color=0x2b2d31)

    view = View()
    create_button = Button(label='Создать обзвон',
                           style=discord.ButtonStyle.success)

    async def create_callback(button_interaction):
        await button_interaction.response.send_modal(CreateObzvonModal())

    create_button.callback = create_callback
    view.add_item(create_button)

    await interaction.response.send_message(embed=embed, view=view)


class CreateObzvonModal(Modal, title='Создание обзвона'):
    name = TextInput(label='Название обзвона', placeholder='Например: Лидеры')

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name.value
        guild = interaction.guild

        category = await guild.create_category(f'Обзвон на {name}')

        role_wait = await guild.create_role(name='Ожидание обзвона')
        role_call = await guild.create_role(name='Проходит обзвон')
        role_end = await guild.create_role(name='Итоги')

        overwrites = {
            guild.default_role:
            discord.PermissionOverwrite(view_channel=False),
            role_wait: discord.PermissionOverwrite(connect=True,
                                                   view_channel=True),
            role_call: discord.PermissionOverwrite(connect=True,
                                                   view_channel=True),
            role_end: discord.PermissionOverwrite(connect=True,
                                                  view_channel=True)
        }

        ch1 = await guild.create_voice_channel('🌑 Ожидание Обзвона',
                                               category=category,
                                               overwrites=overwrites)
        ch2 = await guild.create_voice_channel('🌓 Проходит Обзвон',
                                               category=category,
                                               overwrites=overwrites)
        ch3 = await guild.create_voice_channel('🌕 Ожидание итогов',
                                               category=category,
                                               overwrites=overwrites)

        text_channel = await guild.create_text_channel('📋 настройки обзвона',
                                                       category=category)
        await text_channel.send(view=ObzvonControlView(
            role_wait, role_call, role_end, [ch1, ch2, ch3], category))

        active_obzvons[category.id] = {
            'timestamp': datetime.utcnow(),
            'channels': [ch1, ch2, ch3],
            'roles': [role_wait, role_call, role_end],
            'category': category,
            'text_channel': text_channel
        }

        await interaction.response.send_message(f'Обзвон {name} создан!',
                                                ephemeral=True)

        # Логируем создание обзвона
        await log_action(
            'calls', guild,
            f'📞 {interaction.user.mention} создал обзвон {name}')


class CreateObzvonView(View):

    @discord.ui.button(label='Создать обзвон', style=discord.ButtonStyle.green)
    async def create_obzvon(self, interaction: discord.Interaction,
                            button: Button):
        await interaction.response.send_modal(CreateObzvonModal())


class MoveSelectView(View):

    def __init__(self, members, role, channel):
        super().__init__(timeout=30)
        self.add_item(MoveSelect(members, role, channel))


class MoveSelect(Select):

    def __init__(self, members, role, channel):
        options = [
            discord.SelectOption(label=member.display_name,
                                 value=str(member.id))
            for member in members[:25]
        ]
        super().__init__(placeholder='Выберите пользователя', options=options)
        self.role = role
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(int(self.values[0]))
        if member:
            for r in interaction.guild.roles:
                if r.name in ['Ожидание обзвона', 'Проходит обзвон', 'Итоги']:
                    await member.remove_roles(r)
            await member.add_roles(self.role)
            if member.voice:
                await member.move_to(self.channel)
            await interaction.response.send_message(
                f'✅ {member.mention} перемещён в {self.channel.name} и получил роль `{self.role.name}`',
                ephemeral=True)

            # Логируем перемещение в обзвоне
            await log_action(
                'calls', interaction.guild,
                f'🔄 {interaction.user.mention} переместил {member.mention} в {self.channel.name}'
            )
        else:
            await interaction.response.send_message('⛔ Участник не найден',
                                                    ephemeral=True)


class ObzvonControlView(View):

    def __init__(self, role_wait, role_call, role_end, voice_channels,
                 category):
        super().__init__(timeout=None)
        self.role_wait = role_wait
        self.role_call = role_call
        self.role_end = role_end
        self.voice_channels = voice_channels
        self.category = category

    @discord.ui.button(label='Переместить в Ожидание',
                       style=discord.ButtonStyle.primary)
    async def move_to_wait(self, interaction: discord.Interaction,
                           button: Button):
        members = interaction.guild.members
        await interaction.response.send_message('Выберите участника',
                                                view=MoveSelectView(
                                                    members, self.role_wait,
                                                    self.voice_channels[0]),
                                                ephemeral=True)

    @discord.ui.button(label='Переместить в Проходит',
                       style=discord.ButtonStyle.success)
    async def move_to_call(self, interaction: discord.Interaction,
                           button: Button):
        members = interaction.guild.members
        await interaction.response.send_message('Выберите участника',
                                                view=MoveSelectView(
                                                    members, self.role_call,
                                                    self.voice_channels[1]),
                                                ephemeral=True)

    @discord.ui.button(label='Переместить в Итоги',
                       style=discord.ButtonStyle.secondary)
    async def move_to_end(self, interaction: discord.Interaction,
                          button: Button):
        members = interaction.guild.members
        await interaction.response.send_message('Выберите участника',
                                                view=MoveSelectView(
                                                    members, self.role_end,
                                                    self.voice_channels[2]),
                                                ephemeral=True)

    @discord.ui.button(label='Завершить обзвон',
                       style=discord.ButtonStyle.danger)
    async def end_obzvon(self, interaction: discord.Interaction,
                         button: Button):
        data = active_obzvons.get(self.category.id)
        if data:
            for ch in data['channels']:
                await ch.delete()
            for role in data['roles']:
                await role.delete()
            await data['text_channel'].delete()
            await data['category'].delete()
            del active_obzvons[self.category.id]
            await interaction.response.send_message('Обзвон удалён.',
                                                    ephemeral=True)

            # Логируем завершение обзвона
            await log_action('calls', interaction.guild,
                             f'🔚 {interaction.user.mention} завершил обзвон')


@bot.tree.command(name='обзвон',
                  description='Создание обзвона с каналами и ролями')
async def create_call(interaction: discord.Interaction):
    embed = discord.Embed(
        title='Создание обзвона',
        description='Нажмите кнопку ниже, чтобы начать обзвон.',
        color=discord.Color.blue())
    await interaction.response.send_message(embed=embed,
                                            view=CreateObzvonView(),
                                            ephemeral=True)


# Остальные команды и функции...

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Бот {bot.user} запущен!')
    print('Система логирования активна!')

# ВСТАВЬТЕ ВАШ ТОКЕН БОТА ЗДЕСЬ ↓
BOT_TOKEN = "MTMzMzM1MDY4NTQxMjAzNjYzOA.GvgwY8.hbcyM4P0uoVc0mwZDopD_dCzPjS3FZlogC0loY"

if __name__ == "__main__":
    if BOT_TOKEN == "ВАШ_ТОКЕН_БОТА_ЗДЕСЬ":
        print("❌ ОШИБКА: Вставьте ваш токен бота в переменную BOT_TOKEN!")
        print("Замените 'ВАШ_ТОКЕН_БОТА_ЗДЕСЬ' на реальный токен вашего бота Discord")
        exit(1)
    
    bot.run(BOT_TOKEN)
