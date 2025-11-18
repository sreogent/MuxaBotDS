import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput, Select
import asyncio
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

active_obzvons = {}
reports = {}

# Каналы для логирования
LOG_CHANNELS = {
    "forms": "формы-наказаний",
    "messages": "сообщения",
    "users": "пользователи",
    "voice": "голосовые-каналы",
    "reports": "репорт",
    "private": "приватные-комнаты",
    "calls": "обзвоны",
    "auto_punish": "авто-наказания",
    "moderators": "модераторы",
    "economy": "экономика"
}

# Хранилище настроек серверов
server_settings = {}

# Роли для выдачи (по умолчанию)
AVAILABLE_ROLES = [
    "Новичок", "Участник", "Активный", "VIP", "Модератор", "Администратор"
]


class ReportCreateModal(Modal, title="Создать репорт"):
    description = TextInput(label="Описание проблемы",
                            style=discord.TextStyle.paragraph,
                            placeholder="Опишите детально что произошло...")

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        report_id = f"report-{int(datetime.utcnow().timestamp())}"

        embed = discord.Embed(title="🚨 Новый репорт",
                              description=self.description.value,
                              color=discord.Color.red(),
                              timestamp=datetime.utcnow())
        embed.add_field(name="Автор",
                        value=interaction.user.mention,
                        inline=True)
        embed.add_field(name="Канал", value=self.channel.mention, inline=True)
        embed.add_field(name="ID репорта", value=report_id, inline=False)

        # Отправляем в канал репортов
        report_channel = discord.utils.get(interaction.guild.text_channels,
                                           name="репорт")
        if report_channel:
            view = ReportActionView(report_id, interaction.user, self.channel)
            await report_channel.send(embed=embed, view=view)

        await interaction.response.send_message(
            "✅ Репорт отправлен модераторам!", ephemeral=True)


class ReportActionView(View):

    def __init__(self, report_id, author, channel):
        super().__init__(timeout=None)
        self.report_id = report_id
        self.author = author
        self.channel = channel

    @discord.ui.button(label="Принять",
                       style=discord.ButtonStyle.success,
                       emoji="✅")
    async def accept_report(self, interaction: discord.Interaction,
                            button: Button):
        embed = discord.Embed(
            title="✅ Репорт принят",
            description=
            f"Репорт {self.report_id} принят модератором {interaction.user.mention}",
            color=discord.Color.green())
        await interaction.response.edit_message(embed=embed, view=None)

        # Логируем в канал модераторов
        await log_action(
            "moderators", interaction.guild,
            f"🟢 Репорт {self.report_id} принят модератором {interaction.user.mention}"
        )

    @discord.ui.button(label="Отклонить",
                       style=discord.ButtonStyle.danger,
                       emoji="❌")
    async def decline_report(self, interaction: discord.Interaction,
                             button: Button):
        embed = discord.Embed(
            title="❌ Репорт отклонён",
            description=
            f"Репорт {self.report_id} отклонён модератором {interaction.user.mention}",
            color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=None)

        # Логируем в канал модераторов
        await log_action(
            "moderators", interaction.guild,
            f"🔴 Репорт {self.report_id} отклонён модератором {interaction.user.mention}"
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
            if role.name != "@everyone" and not role.managed
        ]

        # Ограничиваем до 25 ролей (лимит Discord Select)
        server_roles = server_roles[:25]

        options = [
            discord.SelectOption(label=role.name,
                                 value=str(role.id),
                                 description=f"Позиция {role.position}")
            for role in server_roles
        ]

        if not options:
            options = [
                discord.SelectOption(label="Нет доступных ролей", value="none")
            ]

        super().__init__(placeholder="Выберите роль для выдачи",
                         options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message(
                "❌ На сервере нет доступных ролей для выдачи.", ephemeral=True)
            return

        role_id = int(self.values[0])
        role = interaction.guild.get_role(role_id)

        if not role:
            await interaction.response.send_message("❌ Роль не найдена.",
                                                    ephemeral=True)
            return

        await interaction.response.send_message(
            f"Выберите пользователя для выдачи роли `{role.name}`",
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
            discord.SelectOption(label="Ввести ID или упоминание",
                                 value="manual_input",
                                 description="Ввести пользователя вручную")
        ]
        super().__init__(placeholder="Выберите способ",
                         options=options,
                         min_values=1,
                         max_values=1)
        self.role = role

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(UserInputModal(self.role))


class UserInputModal(Modal, title="Выдача роли"):
    user_input = TextInput(label="ID или упоминание пользователя",
                           placeholder="123456789012345678 или @username")

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
                "❌ Пользователь не найден.", ephemeral=True)
            return

        try:
            await member.add_roles(self.role)
            embed = discord.Embed(
                title="✅ Роль выдана",
                description=
                f"Роль `{self.role.name}` выдана пользователю {member.mention}",
                color=discord.Color.green())
            embed.add_field(name="Модератор", value=interaction.user.mention)
            await interaction.response.send_message(embed=embed)

            # Логируем выдачу роли
            await log_action(
                "moderators", interaction.guild,
                f"🎭 {interaction.user.mention} выдал роль `{self.role.name}` пользователю {member.mention}"
            )

        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка при выдаче роли: {str(e)}", ephemeral=True)


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
                                                 name="📊 Логи")
                if not log_category:
                    log_category = await guild.create_category("📊 Логи")

                # Создаем канал логирования
                channel = await guild.create_text_channel(
                    channel_name,
                    category=log_category,
                    topic=f"Автоматическое логирование {log_type}")
                print(f"✅ Создан канал логирования {channel_name}")
            except Exception as e:
                print(f"❌ Не удалось создать канал {channel_name}: {e}")
                return

        if channel:
            try:
                embed = discord.Embed(description=message,
                                      color=discord.Color.blue(),
                                      timestamp=datetime.utcnow())
                embed.set_footer(text=f"Тип лога: {log_type}")
                await channel.send(embed=embed)
            except Exception as e:
                print(f"❌ Ошибка отправки лога в {channel_name}: {e}")


@bot.tree.command(name="репорт",
                  description="Создать репорт по текущему каналу")
async def create_report(interaction: discord.Interaction):
    await interaction.response.send_modal(
        ReportCreateModal(interaction.channel))


@bot.tree.command(name="роль", description="Выдать роль пользователю")
async def give_role(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message(
            "❌ У вас нет прав на выдачу ролей.", ephemeral=True)
        return

    await interaction.response.send_message("Выберите роль для выдачи",
                                            view=RoleSelectView(
                                                interaction.guild),
                                            ephemeral=True)


@bot.tree.command(name="создать_обзвон_бот",
                  description="Создать обзвон как BLACK CHANNEL BOT")
async def create_bot_call(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Обзвон",
        description=
        "Вы можете создать специальную категорию со всеми необходимыми каналами и требуемым функционалом для удобного проведения обзвонов.",
        color=0x2b2d31)

    view = View()
    create_button = Button(label="Создать обзвон",
                           style=discord.ButtonStyle.success)

    async def create_callback(button_interaction):
        await button_interaction.response.send_modal(CreateObzvonModal())

    create_button.callback = create_callback
    view.add_item(create_button)

    await interaction.response.send_message(embed=embed, view=view)


class CreateObzvonModal(Modal, title="Создание обзвона"):
    name = TextInput(label="Название обзвона", placeholder="Например Лидеры")

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name.value
        guild = interaction.guild

        category = await guild.create_category(f"Обзвон на {name}")

        role_wait = await guild.create_role(name="Ожидание обзвона")
        role_call = await guild.create_role(name="Проходит обзвон")
        role_end = await guild.create_role(name="Итоги")

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

        ch1 = await guild.create_voice_channel("🌑 Ожидание Обзвона",
                                               category=category,
                                               overwrites=overwrites)
        ch2 = await guild.create_voice_channel("🌓 Проходит Обзвон",
                                               category=category,
                                               overwrites=overwrites)
        ch3 = await guild.create_voice_channel("🌕 Ожидание итогов",
                                               category=category,
                                               overwrites=overwrites)

        text_channel = await guild.create_text_channel("📋 настройки обзвона",
                                                       category=category)
        await text_channel.send(view=ObzvonControlView(
            role_wait, role_call, role_end, [ch1, ch2, ch3], category))

        active_obzvons[category.id] = {
            "timestamp": datetime.utcnow(),
            "channels": [ch1, ch2, ch3],
            "roles": [role_wait, role_call, role_end],
            "category": category,
            "text_channel": text_channel
        }

        await interaction.response.send_message(f"Обзвон {name} создан!",
                                                ephemeral=True)

        # Логируем создание обзвона
        await log_action(
            "calls", guild,
            f"📞 {interaction.user.mention} создал обзвон {name}")


class CreateObzvonView(View):

    @discord.ui.button(label="Создать обзвон", style=discord.ButtonStyle.green)
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
        super().__init__(placeholder="Выберите пользователя", options=options)
        self.role = role
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(int(self.values[0]))
        if member:
            for r in interaction.guild.roles:
                if r.name in ["Ожидание обзвона", "Проходит обзвон", "Итоги"]:
                    await member.remove_roles(r)
            await member.add_roles(self.role)
            if member.voice:
                await member.move_to(self.channel)
            await interaction.response.send_message(
                f"✅ {member.mention} перемещён в {self.channel.name} и получил роль `{self.role.name}`",
                ephemeral=True)

            # Логируем перемещение в обзвоне
            await log_action(
                "calls", interaction.guild,
                f"🔄 {interaction.user.mention} переместил {member.mention} в {self.channel.name}"
            )
        else:
            await interaction.response.send_message("⛔ Участник не найден",
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

    @discord.ui.button(label="Переместить в Ожидание",
                       style=discord.ButtonStyle.primary)
    async def move_to_wait(self, interaction: discord.Interaction,
                           button: Button):
        members = interaction.guild.members
        await interaction.response.send_message("Выберите участника",
                                                view=MoveSelectView(
                                                    members, self.role_wait,
                                                    self.voice_channels[0]),
                                                ephemeral=True)

    @discord.ui.button(label="Переместить в Проходит",
                       style=discord.ButtonStyle.success)
    async def move_to_call(self, interaction: discord.Interaction,
                           button: Button):
        members = interaction.guild.members
        await interaction.response.send_message("Выберите участника",
                                                view=MoveSelectView(
                                                    members, self.role_call,
                                                    self.voice_channels[1]),
                                                ephemeral=True)

    @discord.ui.button(label="Переместить в Итоги",
                       style=discord.ButtonStyle.secondary)
    async def move_to_end(self, interaction: discord.Interaction,
                          button: Button):
        members = interaction.guild.members
        await interaction.response.send_message("Выберите участника",
                                                view=MoveSelectView(
                                                    members, self.role_end,
                                                    self.voice_channels[2]),
                                                ephemeral=True)

    @discord.ui.button(label="Завершить обзвон",
                       style=discord.ButtonStyle.danger)
    async def end_obzvon(self, interaction: discord.Interaction,
                         button: Button):
        data = active_obzvons.get(self.category.id)
        if data:
            for ch in data["channels"]:
                await ch.delete()
            for role in data["roles"]:
                await role.delete()
            await data["text_channel"].delete()
            await data["category"].delete()
            del active_obzvons[self.category.id]
            await interaction.response.send_message("Обзвон удалён.",
                                                    ephemeral=True)

            # Логируем завершение обзвона
            await log_action("calls", interaction.guild,
                             f"🔚 {interaction.user.mention} завершил обзвон")


class ReportActionButtonsView(View):

    def __init__(self, report_id, target, reporter):
        super().__init__(timeout=None)
        self.report_id = report_id
        self.target = target
        self.reporter = reporter

    @discord.ui.button(label="Одобрить",
                       style=discord.ButtonStyle.success,
                       emoji="✅")
    async def approve_report(self, interaction: discord.Interaction,
                             button: Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message(
                "❌ У вас нет прав для обработки жалоб.", ephemeral=True)
            return

        embed = discord.Embed(
            title="✅ Жалоба одобрена",
            description=
            f"Жалоба на {self.target.mention} одобрена модератором {interaction.user.mention}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow())

        # Убираем кнопки
        await interaction.response.edit_message(embed=embed, view=None)

        # Удаляем из активных жалоб
        if self.report_id in reports:
            del reports[self.report_id]

        # Логируем решение
        await log_action(
            "reports", interaction.guild,
            f"✅ {interaction.user.mention} одобрил жалобу на {self.target.mention}"
        )

    @discord.ui.button(label="Отклонить",
                       style=discord.ButtonStyle.danger,
                       emoji="❌")
    async def decline_report(self, interaction: discord.Interaction,
                             button: Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message(
                "❌ У вас нет прав для обработки жалоб.", ephemeral=True)
            return

        embed = discord.Embed(
            title="❌ Жалоба отклонена",
            description=
            f"Жалоба на {self.target.mention} отклонена модератором {interaction.user.mention}",
            color=discord.Color.red(),
            timestamp=datetime.utcnow())

        # Убираем кнопки
        await interaction.response.edit_message(embed=embed, view=None)

        # Удаляем из активных жалоб
        if self.report_id in reports:
            del reports[self.report_id]

        # Логируем решение
        await log_action(
            "reports", interaction.guild,
            f"❌ {interaction.user.mention} отклонил жалобу на {self.target.mention}"
        )


class ReportModal(Modal, title="Жалоба на участника"):
    reason = TextInput(label="Причина", style=discord.TextStyle.paragraph)

    def __init__(self, target):
        super().__init__()
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):
        report_id = f"{interaction.guild_id}-{interaction.user.id}-{int(datetime.utcnow().timestamp())}"
        reports[report_id] = {
            "target": self.target,
            "reason": self.reason.value,
            "reporter": interaction.user,
            "timestamp": datetime.utcnow()
        }

        embed = discord.Embed(title="🚨 Новая жалоба",
                              color=discord.Color.red(),
                              timestamp=datetime.utcnow())
        embed.add_field(name="На пользователя",
                        value=self.target.mention,
                        inline=True)
        embed.add_field(name="От пользователя",
                        value=interaction.user.mention,
                        inline=True)
        embed.add_field(name="ID жалобы", value=report_id, inline=False)
        embed.add_field(name="Причина", value=self.reason.value, inline=False)

        await interaction.response.send_message(
            "✅ Жалоба отправлена модераторам!", ephemeral=True)

        # Ищем канал для жалоб
        report_channel = None
        for channel_name in ["жалобы", "репорт", "reports"]:
            report_channel = discord.utils.get(interaction.guild.text_channels,
                                               name=channel_name)
            if report_channel:
                break

        if report_channel:
            view = ReportActionButtonsView(report_id, self.target,
                                           interaction.user)
            await report_channel.send(embed=embed, view=view)
        else:
            # Если канала нет, пытаемся создать
            try:
                report_channel = await interaction.guild.create_text_channel(
                    "жалобы")
                view = ReportActionButtonsView(report_id, self.target,
                                               interaction.user)
                await report_channel.send(embed=embed, view=view)
            except:
                pass

        # Логируем жалобу
        await log_action(
            "reports", interaction.guild,
            f"📋 {interaction.user.mention} подал жалобу на {self.target.mention}. Причина: {self.reason.value}"
        )


user_warnings = {}


@bot.tree.command(name="варн", description="Выдать предупреждение участнику")
async def warn(interaction: discord.Interaction,
               member: discord.Member,
               reason: str = "Не указана"):
    if member.id not in user_warnings:
        user_warnings[member.id] = 0

    user_warnings[member.id] += 1
    warnings_count = user_warnings[member.id]

    embed = discord.Embed(title="⚠️ Предупреждение",
                          color=discord.Color.orange())
    embed.add_field(name="Участник", value=member.mention)
    embed.add_field(name="Модератор", value=interaction.user.mention)
    embed.add_field(name="Причина", value=reason, inline=False)
    embed.add_field(name="Количество предупреждений",
                    value=f"{warnings_count}/3")

    if warnings_count == 3:
        try:
            await member.ban(
                reason=f"3 предупреждения. Последняя причина: {reason}")
            embed.add_field(name="Действие",
                            value="🔨 Забанен за 3 предупреждения",
                            inline=False)
            user_warnings[member.id] = 0

            # Логируем автобан
            await log_action(
                "auto_punish", interaction.guild,
                f"🔨 {member.mention} автоматически забанен за 3 предупреждения"
            )
        except:
            embed.add_field(name="Ошибка",
                            value="Не удалось забанить пользователя",
                            inline=False)

    await interaction.response.send_message(embed=embed)

    # Логируем предупреждение
    await log_action(
        "forms", interaction.guild,
        f"⚠️ {interaction.user.mention} выдал предупреждение {member.mention}. Причина: {reason}"
    )


@bot.tree.command(name="снять_варн",
                  description="Снять предупреждение у участника")
async def remove_warn(interaction: discord.Interaction,
                      member: discord.Member):
    if member.id not in user_warnings or user_warnings[member.id] == 0:
        await interaction.response.send_message(
            f"У {member.mention} нет предупреждений.", ephemeral=True)
        return

    user_warnings[member.id] -= 1
    warnings_count = user_warnings[member.id]

    embed = discord.Embed(title="✅ Предупреждение снято",
                          color=discord.Color.green())
    embed.add_field(name="Участник", value=member.mention)
    embed.add_field(name="Модератор", value=interaction.user.mention)
    embed.add_field(name="Осталось предупреждений",
                    value=f"{warnings_count}/3")

    await interaction.response.send_message(embed=embed)

    # Логируем снятие предупреждения
    await log_action(
        "forms", interaction.guild,
        f"✅ {interaction.user.mention} снял предупреждение с {member.mention}")


@bot.tree.command(name="кик", description="Исключить участника с сервера")
async def kick(interaction: discord.Interaction,
               member: discord.Member,
               reason: str = "Не указана"):
    try:
        await member.kick(reason=reason)
        embed = discord.Embed(title="👟 Участник исключён",
                              color=discord.Color.orange())
        embed.add_field(name="Участник", value=member.mention)
        embed.add_field(name="Модератор", value=interaction.user.mention)
        embed.add_field(name="Причина", value=reason, inline=False)
        await interaction.response.send_message(embed=embed)

        # Логируем кик
        await log_action(
            "forms", interaction.guild,
            f"👟 {interaction.user.mention} исключил {member.mention}. Причина: {reason}"
        )
    except:
        await interaction.response.send_message(
            "❌ Не удалось исключить участника.", ephemeral=True)


@bot.tree.command(name="бан", description="Заблокировать участника навсегда")
async def ban(interaction: discord.Interaction,
              member: discord.Member,
              reason: str = "Не указана"):
    try:
        await member.ban(reason=reason)
        if member.id in user_warnings:
            user_warnings[member.id] = 0
        embed = discord.Embed(title="🔨 Участник заблокирован",
                              color=discord.Color.red())
        embed.add_field(name="Участник", value=member.mention)
        embed.add_field(name="Модератор", value=interaction.user.mention)
        embed.add_field(name="Причина", value=reason, inline=False)
        await interaction.response.send_message(embed=embed)

        # Логируем бан
        await log_action(
            "forms", interaction.guild,
            f"🔨 {interaction.user.mention} заблокировал {member.mention}. Причина: {reason}"
        )
    except:
        await interaction.response.send_message(
            "❌ Не удалось заблокировать участника.", ephemeral=True)


@bot.tree.command(name="мут", description="Заглушить участника на 5 минут")
async def mute(interaction: discord.Interaction, member: discord.Member):
    duration = timedelta(minutes=5)
    try:
        await member.timeout(until=datetime.utcnow() + duration)
        await interaction.response.send_message(
            f"🔇 {member.mention} получил мут на 5 минут.")

        # Логируем мут
        await log_action(
            "forms", interaction.guild,
            f"🔇 {interaction.user.mention} выдал мут {member.mention} на 5 минут"
        )
    except:
        await interaction.response.send_message("❌ Не удалось выдать мут.",
                                                ephemeral=True)


@bot.tree.command(name="снять", description="Снять мут у участника")
async def unmute(interaction: discord.Interaction, member: discord.Member):
    try:
        await member.timeout(until=None)
        await interaction.response.send_message(
            f"🔊 Мут снят с {member.mention}.")

        # Логируем снятие мута
        await log_action(
            "forms", interaction.guild,
            f"🔊 {interaction.user.mention} снял мут с {member.mention}")
    except:
        await interaction.response.send_message("❌ Не удалось снять мут.",
                                                ephemeral=True)


@bot.tree.command(name="жалоба", description="Подать жалобу на участника")
async def report_command(interaction: discord.Interaction,
                         member: discord.Member):
    if member == interaction.user:
        await interaction.response.send_message(
            "Вы не можете пожаловаться на себя!", ephemeral=True)
    else:
        await interaction.response.send_modal(ReportModal(member))


@bot.tree.command(name="жалобы", description="Посмотреть все активные жалобы")
async def view_reports(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message(
            "❌ У вас нет прав для просмотра жалоб.", ephemeral=True)
        return

    if not reports:
        await interaction.response.send_message("📋 Активных жалоб нет.",
                                                ephemeral=True)
        return

    embed = discord.Embed(title="📋 Активные жалобы",
                          color=discord.Color.blue())

    for report_id, report_data in list(reports.items())[:10]:
        target = report_data["target"]
        reporter = report_data["reporter"]
        reason = report_data["reason"]
        timestamp = report_data["timestamp"].strftime("%d.%m.%Y %H:%M")

        embed.add_field(
            name=f"Жалоба на {target.display_name}",
            value=
            f"От: {reporter.display_name}\nПричина: {reason}\nВремя: {timestamp}",
            inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="обзвон",
                  description="Создание обзвона с каналами и ролями")
async def create_call(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Создание обзвона",
        description="Нажмите кнопку ниже, чтобы начать обзвон.",
        color=discord.Color.blue())
    await interaction.response.send_message(embed=embed,
                                            view=CreateObzvonView(),
                                            ephemeral=True)


class VerificationView(View):

    def __init__(self, verification_roles=None):
        super().__init__(timeout=None)
        self.verification_roles = verification_roles or []

    @discord.ui.button(label="✅ Верифицироваться",
                       style=discord.ButtonStyle.success,
                       emoji="✅")
    async def verify_user(self, interaction: discord.Interaction,
                          button: Button):
        guild = interaction.guild
        guild_id = guild.id

        # Получаем настройки сервера
        if guild_id not in server_settings:
            await interaction.response.send_message(
                "❌ Верификация не настроена на этом сервере.", ephemeral=True)
            return

        # Проверяем, есть ли настроенные роли для верификации
        verification_roles = server_settings[guild_id].get(
            "verification_roles", [])
        if not verification_roles:
            await interaction.response.send_message(
                "❌ Роли верификации не настроены.", ephemeral=True)
            return

        # Если есть несколько ролей, показываем выбор
        if len(verification_roles) > 1:
            # Показываем выбор ролей
            await interaction.response.send_message(
                "Выберите роль для получения",
                view=VerificationRoleSelectView(verification_roles, guild),
                ephemeral=True)
        else:
            # Если роль одна, выдаем её сразу
            role_id = verification_roles[0]
            role = guild.get_role(role_id)
            if role:
                try:
                    await interaction.user.add_roles(role)
                    await interaction.response.send_message(
                        f"✅ Вы успешно верифицированы! Вам выдана роль {role.mention}",
                        ephemeral=True)
                    await log_action(
                        "users", guild,
                        f"✅ {interaction.user.mention} прошел верификацию и получил роль {role.mention}"
                    )
                except Exception as e:
                    await interaction.response.send_message(
                        f"❌ Ошибка при выдаче роли: {str(e)}", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "❌ Роль верификации не найдена.", ephemeral=True)


class VerificationRoleSelectView(View):

    def __init__(self, role_ids, guild):
        super().__init__(timeout=60)
        self.role_ids = role_ids
        self.add_item(VerificationRoleSelect(role_ids, guild))


class VerificationRoleSelect(Select):

    def __init__(self, role_ids, guild):
        options = []
        for role_id in role_ids[:25]:
            role = guild.get_role(role_id)
            if role:
                options.append(
                    discord.SelectOption(
                        label=role.name,
                        value=str(role.id),
                        description=f"Получить роль {role.name}"))

        if not options:
            options = [
                discord.SelectOption(label="Нет доступных ролей", value="none")
            ]

        super().__init__(placeholder="Выберите роль для верификации",
                         options=options,
                         min_values=1,
                         max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message(
                "❌ Нет доступных ролей для верификации.", ephemeral=True)
            return

        role_id = int(self.values[0])
        role = interaction.guild.get_role(role_id)

        if not role:
            await interaction.response.send_message("❌ Роль не найдена.",
                                                    ephemeral=True)
            return

        try:
            await interaction.user.add_roles(role)
            await interaction.response.edit_message(
                content=
                f"✅ Вы успешно верифицированы! Вам выдана роль {role.mention}",
                view=None)

            # Логируем верификацию
            await log_action(
                "users", interaction.guild,
                f"✅ {interaction.user.mention} прошел верификацию и получил роль {role.mention}"
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка при выдаче роли: {str(e)}", ephemeral=True)


class ComplaintView(View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📝 Подать жалобу",
                       style=discord.ButtonStyle.primary,
                       emoji="📝")
    async def submit_complaint(self, interaction: discord.Interaction,
                               button: Button):
        await interaction.response.send_modal(ComplaintModal())


class ComplaintModal(Modal, title="Подача жалобы"):
    target_user = TextInput(label="На кого жалоба (ID или @упоминание)",
                            placeholder="123456789 или @username")
    reason = TextInput(label="Причина жалобы",
                       style=discord.TextStyle.paragraph,
                       placeholder="Опишите подробно причину жалобы...")
    evidence = TextInput(
        label="Доказательства (ссылки на скриншоты)",
        style=discord.TextStyle.paragraph,
        required=False,
        placeholder="Ссылки на изображения или дополнительная информация")

    async def on_submit(self, interaction: discord.Interaction):
        # Пытаемся найти пользователя
        user_str = self.target_user.value.strip()
        target_member = None

        if user_str.startswith('<@') and user_str.endswith('>'):
            user_id = user_str[2:-1].replace('!', '')
            try:
                target_member = interaction.guild.get_member(int(user_id))
            except:
                pass
        elif user_str.isdigit():
            try:
                target_member = interaction.guild.get_member(int(user_str))
            except:
                pass
        else:
            target_member = discord.utils.get(interaction.guild.members,
                                              name=user_str)

        if not target_member:
            await interaction.response.send_message(
                "❌ Пользователь не найден. Проверьте правильность ID или упоминания.",
                ephemeral=True)
            return

        if target_member == interaction.user:
            await interaction.response.send_message(
                "❌ Вы не можете подать жалобу на себя!", ephemeral=True)
            return

        # Создаем ID жалобы
        report_id = f"{interaction.guild_id}-{interaction.user.id}-{int(datetime.utcnow().timestamp())}"

        # Сохраняем жалобу
        reports[report_id] = {
            "target": target_member,
            "reason": self.reason.value,
            "evidence":
            self.evidence.value if self.evidence.value else "Не предоставлены",
            "reporter": interaction.user,
            "timestamp": datetime.utcnow()
        }

        # Ищем или создаем категорию для репортов
        report_category = discord.utils.get(interaction.guild.categories,
                                            name="📋 РЕПОРТЫ")
        if not report_category:
            try:
                report_category = await interaction.guild.create_category(
                    "📋 РЕПОРТЫ")
            except:
                pass

        # Создаем отдельный канал для этого репорта
        report_channel_name = f"репорт-{int(datetime.utcnow().timestamp())}"
        try:
            report_channel = await interaction.guild.create_text_channel(
                report_channel_name,
                category=report_category,
                topic=
                f"Жалоба от {interaction.user.display_name} на {target_member.display_name}"
            )

            # Создаем embed для жалобы
            embed = discord.Embed(title="🚨 Новая жалоба",
                                  color=discord.Color.red(),
                                  timestamp=datetime.utcnow())
            embed.add_field(name="👤 На пользователя",
                            value=target_member.mention,
                            inline=True)
            embed.add_field(name="👮 От пользователя",
                            value=interaction.user.mention,
                            inline=True)
            embed.add_field(name="🆔 ID жалобы",
                            value=f"`{report_id}`",
                            inline=False)
            embed.add_field(name="📋 Причина",
                            value=self.reason.value,
                            inline=False)
            embed.add_field(name="🔍 Доказательства",
                            value=self.evidence.value
                            if self.evidence.value else "Не предоставлены",
                            inline=False)
            embed.set_footer(
                text=f"Подана пользователем {interaction.user.display_name}")

            # Создаем кнопку для рассмотрения
            view = ComplaintReviewView(report_id, target_member,
                                       interaction.user, report_channel)
            await report_channel.send(embed=embed, view=view)

            # Уведомляем пользователя в личные сообщения
            try:
                await interaction.user.send(
                    f"✅ Ваша жалоба принята! Канал для обсуждения: {report_channel.mention}"
                )
            except:
                pass

            await interaction.response.send_message(
                f"✅ Ваша жалоба отправлена! Создан отдельный канал {report_channel.mention}",
                ephemeral=True)

            # Логируем подачу жалобы
            await log_action(
                "reports", interaction.guild,
                f"📝 {interaction.user.mention} подал жалобу на {target_member.mention}. Создан канал {report_channel.mention}"
            )

        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка при создании канала репорта: {str(e)}",
                ephemeral=True)


class ComplaintResponseModal(Modal, title="Ответ на жалобу"):
    response_text = TextInput(label="Ваш ответ",
                              style=discord.TextStyle.paragraph,
                              placeholder="Напишите свой ответ по жалобе...",
                              required=True,
                              max_length=2000)

    def __init__(self, channel, reporter, moderator):
        super().__init__()
        self.channel = channel
        self.reporter = reporter
        self.moderator = moderator

    async def on_submit(self, interaction: discord.Interaction):
        # Создаем embed для ответа
        embed = discord.Embed(title="💬 Ответ модератора",
                              description=self.response_text.value,
                              color=discord.Color.blue(),
                              timestamp=datetime.utcnow())
        embed.set_author(name=self.moderator.display_name,
                         icon_url=self.moderator.display_avatar.url)
        embed.set_footer(text="Ответ от модератора")

        # Отправляем ответ в канал жалобы
        await self.channel.send(embed=embed)

        # Уведомляем подателя жалобы
        try:
            await self.reporter.send(
                f"💬 Модератор {self.moderator.display_name} оставил ответ на вашу жалобу\n\n{self.response_text.value}"
            )
        except:
            pass

        await interaction.response.send_message(
            "✅ Ваш ответ отправлен в канал жалобы и пользователю!",
            ephemeral=True)

        # Логируем
        await log_action(
            "reports", interaction.guild,
            f"💬 {self.moderator.mention} оставил ответ в канале жалобы {self.channel.mention}"
        )


class ComplaintReviewView(View):

    def __init__(self, report_id, target, reporter, channel):
        super().__init__(timeout=None)
        self.report_id = report_id
        self.target = target
        self.reporter = reporter
        self.channel = channel

    @discord.ui.button(label="✅ Принять жалобу",
                       style=discord.ButtonStyle.success,
                       emoji="✅")
    async def accept_complaint(self, interaction: discord.Interaction,
                               button: Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message(
                "❌ У вас нет прав для рассмотрения жалоб.", ephemeral=True)
            return

        embed = discord.Embed(
            title="✅ Жалоба принята",
            description=
            f"Жалоба на {self.target.mention} принята модератором {interaction.user.mention}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow())

        await interaction.response.edit_message(embed=embed, view=None)

        # Отправляем сообщение в канал
        await self.channel.send(
            f"✅ Решение: Жалоба принята модератором {interaction.user.mention}.\nДальнейшие действия будут предприняты в отношении {self.target.mention}"
        )

        # Уведомляем подателя жалобы
        try:
            await self.reporter.send(
                f"✅ Ваша жалоба на {self.target.display_name} была принята модератором. Спасибо за бдительность!"
            )
        except:
            pass

        # Удаляем из активных жалоб
        if self.report_id in reports:
            del reports[self.report_id]

        # Логируем
        await log_action(
            "reports", interaction.guild,
            f"✅ {interaction.user.mention} принял жалобу на {self.target.mention}"
        )

    @discord.ui.button(label="❌ Отклонить жалобу",
                       style=discord.ButtonStyle.danger,
                       emoji="❌")
    async def decline_complaint(self, interaction: discord.Interaction,
                                button: Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message(
                "❌ У вас нет прав для рассмотрения жалоб.", ephemeral=True)
            return

        embed = discord.Embed(
            title="❌ Жалоба отклонена",
            description=
            f"Жалоба на {self.target.mention} отклонена модератором {interaction.user.mention}",
            color=discord.Color.red(),
            timestamp=datetime.utcnow())

        await interaction.response.edit_message(embed=embed, view=None)

        # Отправляем сообщение в канал
        await self.channel.send(
            f"❌ Решение: Жалоба отклонена модератором {interaction.user.mention}.\nОснований для дальнейших действий не обнаружено."
        )

        # Уведомляем подателя жалобы
        try:
            await self.reporter.send(
                f"❌ Ваша жалоба на {self.target.display_name} была отклонена после рассмотрения модератором."
            )
        except:
            pass

        # Удаляем из активных жалоб
        if self.report_id in reports:
            del reports[self.report_id]

        # Логируем
        await log_action(
            "reports", interaction.guild,
            f"❌ {interaction.user.mention} отклонил жалобу на {self.target.mention}"
        )

    @discord.ui.button(label="💬 Дать ответ",
                       style=discord.ButtonStyle.primary,
                       emoji="💬")
    async def give_response(self, interaction: discord.Interaction,
                            button: Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message(
                "❌ У вас нет прав для ответа на жалобы.", ephemeral=True)
            return

        # Открываем модальное окно для ввода ответа
        modal = ComplaintResponseModal(channel=self.channel,
                                       reporter=self.reporter,
                                       moderator=interaction.user)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑️ Закрыть канал",
                       style=discord.ButtonStyle.secondary,
                       emoji="🗑️")
    async def close_channel(self, interaction: discord.Interaction,
                            button: Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message(
                "❌ У вас нет прав для закрытия канала.", ephemeral=True)
            return

        await interaction.response.send_message(
            "🗑️ Канал будет удален через 5 секунд...", ephemeral=False)

        # Удаляем из активных жалоб
        if self.report_id in reports:
            del reports[self.report_id]

        # Логируем
        await log_action(
            "reports", interaction.guild,
            f"🗑️ {interaction.user.mention} закрыл канал жалобы {self.channel.name}"
        )

        await asyncio.sleep(5)
        try:
            await self.channel.delete()
        except:
            pass


@bot.tree.command(name="сказать",
                  description="Отправить сообщение от имени бота")
async def bot_say(interaction: discord.Interaction, канал: discord.TextChannel,
                  сообщение: str):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message(
            "❌ У вас нет прав для отправки сообщений от имени бота.",
            ephemeral=True)
        return

    try:
        await канал.send(сообщение)
        await interaction.response.send_message(
            f"✅ Сообщение отправлено в {канал.mention}", ephemeral=True)

        # Логируем отправку сообщения от имени бота
        await log_action(
            "moderators", interaction.guild,
            f"🤖 {interaction.user.mention} отправил сообщение от имени бота в {канал.mention}: {сообщение}"
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Ошибка при отправке сообщения: {str(e)}", ephemeral=True)


@bot.tree.command(
    name="сказать_embed",
    description="Отправить красивое сообщение (embed) от имени бота")
async def bot_say_embed(interaction: discord.Interaction,
                        канал: discord.TextChannel,
                        заголовок: str,
                        описание: str,
                        цвет: str = "синий"):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message(
            "❌ У вас нет прав для отправки сообщений от имени бота.",
            ephemeral=True)
        return

    # Определяем цвет
    color_map = {
        "красный": discord.Color.red(),
        "синий": discord.Color.blue(),
        "зеленый": discord.Color.green(),
        "желтый": discord.Color.yellow(),
        "фиолетовый": discord.Color.purple(),
        "оранжевый": discord.Color.orange(),
        "черный": discord.Color.from_rgb(0, 0, 0),
        "белый": discord.Color.from_rgb(255, 255, 255)
    }

    embed_color = color_map.get(цвет.lower(), discord.Color.blue())

    try:
        embed = discord.Embed(title=заголовок,
                              description=описание,
                              color=embed_color,
                              timestamp=datetime.utcnow())
        embed.set_footer(text=f"Сообщение от {interaction.guild.name}")

        await канал.send(embed=embed)
        await interaction.response.send_message(
            f"✅ Embed-сообщение отправлено в {канал.mention}", ephemeral=True)

        # Логируем отправку embed
        await log_action(
            "moderators", interaction.guild,
            f"🎨 {interaction.user.mention} отправил embed от имени бота в {канал.mention}: {заголовок}"
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Ошибка при отправке embed: {str(e)}", ephemeral=True)


@bot.tree.command(name="канал_жалоб",
                  description="Создать канал для подачи жалоб")
async def create_complaints_channel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message(
            "❌ У вас нет прав для создания каналов.", ephemeral=True)
        return

    # Проверяем, есть ли уже канал жалоб
    existing_channel = discord.utils.get(interaction.guild.text_channels,
                                         name="жалобы")
    if existing_channel:
        await interaction.response.send_message(
            f"❌ Канал жалоб уже существует: {existing_channel.mention}",
            ephemeral=True)
        return

    try:
        # Создаем канал для жалоб
        complaints_channel = await interaction.guild.create_text_channel(
            "жалобы", topic="Канал для подачи жалоб на нарушителей")

        # Создаем embed с инструкцией
        embed = discord.Embed(
            title="📝 Подача жалоб",
            description=
            "Если вы столкнулись с нарушением правил сервера, вы можете подать жалобу, нажав на кнопку ниже.",
            color=discord.Color.blue())
        embed.add_field(
            name="📋 Как подать жалобу",
            value=
            "1. Нажмите кнопку '📝 Подать жалобу'\n2. Укажите пользователя (ID или @упоминание)\n3. Опишите причину жалобы\n4. При необходимости приложите доказательства",
            inline=False)
        embed.add_field(
            name="⚠️ Важно",
            value=
            "• Ложные жалобы караются предупреждением\n• Жалобы рассматриваются модераторами\n• Результат рассмотрения вам сообщат в личные сообщения",
            inline=False)
        embed.set_footer(text="Администрация сервера")

        # Отправляем сообщение с кнопкой в канал жалоб
        view = ComplaintView()
        await complaints_channel.send(embed=embed, view=view)

        await interaction.response.send_message(
            f"✅ Канал жалоб создан: {complaints_channel.mention}",
            ephemeral=True)

        # Логируем создание канала
        await log_action(
            "moderators", interaction.guild,
            f"📝 {interaction.user.mention} создал канал жалоб {complaints_channel.mention}"
        )

    except Exception as e:
        await interaction.response.send_message(
            f"❌ Ошибка при создании канала: {str(e)}", ephemeral=True)


@bot.tree.command(
    name="объявление",
    description="Отправить важное объявление с упоминанием @everyone")
async def announcement(interaction: discord.Interaction,
                       канал: discord.TextChannel, заголовок: str, текст: str):
    if not interaction.user.guild_permissions.mention_everyone:
        await interaction.response.send_message(
            "❌ У вас нет прав для отправки объявлений с @everyone.",
            ephemeral=True)
        return

    try:
        embed = discord.Embed(title=f"📢 {заголовок}",
                              description=текст,
                              color=discord.Color.gold(),
                              timestamp=datetime.utcnow())
        embed.set_footer(
            text=f"Объявление от администрации {interaction.guild.name}")

        await канал.send("@everyone", embed=embed)
        await interaction.response.send_message(
            f"✅ Объявление отправлено в {канал.mention}", ephemeral=True)

        # Логируем объявление
        await log_action(
            "moderators", interaction.guild,
            f"📢 {interaction.user.mention} отправил объявление в {канал.mention}: {заголовок}"
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Ошибка при отправке объявления: {str(e)}", ephemeral=True)


@bot.tree.command(name="настроить_канал_жалоб",
                  description="Настроить канал для подачи жалоб")
async def setup_complaints_channel(interaction: discord.Interaction,
                                   канал: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ У вас нет прав администратора.", ephemeral=True)
        return

    guild_id = interaction.guild_id
    if guild_id not in server_settings:
        server_settings[guild_id] = {}

    server_settings[guild_id]["complaints_channel"] = канал.id

    # Создаем embed с инструкцией для канала жалоб
    embed = discord.Embed(
        title="📝 Подача жалоб",
        description=
        "Если вы столкнулись с нарушением правил сервера, вы можете подать жалобу, нажав на кнопку ниже.",
        color=discord.Color.blue())
    embed.add_field(
        name="📋 Как подать жалобу",
        value=
        "1. Нажмите кнопку '📝 Подать жалобу'\n2. Укажите пользователя (ID или @упоминание)\n3. Опишите причину жалобы\n4. При необходимости приложите доказательства",
        inline=False)
    embed.add_field(
        name="⚠️ Важно",
        value=
        "• Ложные жалобы караются предупреждением\n• Жалобы рассматриваются модераторами\n• Результат рассмотрения вам сообщат в личные сообщения",
        inline=False)
    embed.set_footer(text="Администрация сервера")

    view = ComplaintView()
    await канал.send(embed=embed, view=view)

    await interaction.response.send_message(
        f"✅ Канал {канал.mention} настроен как канал для жалоб!",
        ephemeral=True)

    # Логируем настройку
    await log_action(
        "moderators", interaction.guild,
        f"⚙️ {interaction.user.mention} настроил канал жалоб {канал.mention}")


@bot.tree.command(name="настроить_верификацию",
                  description="Настроить систему верификации с одной ролью")
async def setup_verification(interaction: discord.Interaction,
                             канал: discord.TextChannel, роль: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ У вас нет прав администратора.", ephemeral=True)
        return

    guild_id = interaction.guild_id
    if guild_id not in server_settings:
        server_settings[guild_id] = {}

    server_settings[guild_id]["verification_channel"] = канал.id
    server_settings[guild_id]["verification_role"] = роль.id
    server_settings[guild_id]["verification_roles"] = [роль.id
                                                       ]  # Новая система

    # Создаем embed для верификации
    embed = discord.Embed(
        title="🛡️ Получение доступа",
        description=
        f"Вам необходимо пройти процесс верификации, чтобы воспользоваться Discord сервером в полном объеме и получить доступ ко всем существующим функциям.\n\nЕсли получить доступ не удается — воспользуйтесь официальным приложением Discord, либо обновите его до последней версии и попробуйте снова.",
        color=0x2b2d31)

    # Создаем кнопку верификации
    view = VerificationView([роль.id])
    await канал.send(embed=embed, view=view)

    await interaction.response.send_message(
        f"✅ Верификация настроена!\nКанал: {канал.mention}\nРоль: {роль.mention}",
        ephemeral=True)

    # Логируем настройку верификации
    await log_action(
        "moderators", interaction.guild,
        f"🛡️ {interaction.user.mention} настроил верификацию в {канал.mention} с ролью {роль.mention}"
    )


class MultiVerificationSetupModal(Modal, title="Настройка мультиверификации"):
    role_ids_input = TextInput(label="ID ролей (через запятую)",
                               placeholder="123456789,987654321,111222333",
                               style=discord.TextStyle.paragraph,
                               required=True)

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        try:
            role_ids_str = self.role_ids_input.value.strip()
            role_ids = [int(rid.strip()) for rid in role_ids_str.split(',')]

            guild_id = interaction.guild_id
            if guild_id not in server_settings:
                server_settings[guild_id] = {}

            server_settings[guild_id]["verification_channel"] = self.channel.id
            server_settings[guild_id]["verification_roles"] = role_ids

            embed = discord.Embed(
                title="🛡️ Получение доступа",
                description=
                f"Вам необходимо пройти процесс верификации, чтобы воспользоваться Discord сервером в полном объеме и получить доступ ко всем существующим функциям.\n\nЕсли получить доступ не удается — воспользуйтесь официальным приложением Discord, либо обновите его до последней версии и попробуйте снова.",
                color=0x2b2d31)

            view = VerificationView(role_ids)
            await self.channel.send(embed=embed, view=view)

            role_mentions = []
            for role_id in role_ids:
                role = interaction.guild.get_role(role_id)
                if role:
                    role_mentions.append(role.mention)

            await interaction.response.send_message(
                f"✅ Мультиверификация настроена!\nКанал: {self.channel.mention}\nРоли: {', '.join(role_mentions)}",
                ephemeral=True)

            await log_action(
                "moderators", interaction.guild,
                f"🛡️ {interaction.user.mention} настроил мультиверификацию в {self.channel.mention}"
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка: {str(e)}. Проверьте правильность ID ролей.",
                ephemeral=True)


@bot.tree.command(
    name="настроить_мульти_верификацию",
    description="Настроить верификацию с выбором из нескольких ролей")
async def setup_multi_verification(interaction: discord.Interaction,
                                   канал: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ У вас нет прав администратора.", ephemeral=True)
        return

    await interaction.response.send_modal(MultiVerificationSetupModal(канал))


# События для логирования
@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    await log_action(
        "messages", message.guild,
        f"🗑️ Сообщение от {message.author.mention} удалено в {message.channel.mention}"
    )


@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content:
        return
    await log_action(
        "messages", before.guild,
        f"✏️ {before.author.mention} изменил сообщение в {before.channel.mention}"
    )


@bot.event
async def on_member_join(member):
    await log_action("users", member.guild,
                     f"📥 {member.mention} присоединился к серверу")

    # Проверяем, настроена ли верификация на сервере
    guild_id = member.guild.id
    if guild_id in server_settings and "verification_channel" in server_settings[
            guild_id]:
        verification_channel_id = server_settings[guild_id][
            "verification_channel"]
        verification_channel = member.guild.get_channel(
            verification_channel_id)

        if verification_channel:
            try:
                # Отправляем личное сообщение с инструкцией
                embed = discord.Embed(
                    title=f"🎉 Добро пожаловать на сервер {member.guild.name}!",
                    description=
                    f"Для получения полного доступа к серверу, пройдите верификацию в канале {verification_channel.mention}",
                    color=discord.Color.green())
                await member.send(embed=embed)
            except:
                # Если не удается отправить ЛС, игнорируем ошибку
                pass


@bot.event
async def on_member_remove(member):
    await log_action("users", member.guild,
                     f"📤 {member.mention} покинул сервер")


@bot.event
async def on_voice_state_update(member, before, after):
    guild = member.guild

    if before.channel != after.channel:
        if before.channel and after.channel:
            await log_action(
                "voice", guild,
                f"🔄 {member.mention} переместился из {before.channel.name} в {after.channel.name}"
            )
        elif after.channel:
            await log_action(
                "voice", guild,
                f"🔊 {member.mention} подключился к {after.channel.name}")
        elif before.channel:
            await log_action(
                "voice", guild,
                f"🔇 {member.mention} отключился от {before.channel.name}")


@bot.event
async def on_member_update(before, after):
    if before.roles != after.roles:
        added_roles = set(after.roles) - set(before.roles)
        removed_roles = set(before.roles) - set(after.roles)

        for role in added_roles:
            await log_action("users", before.guild,
                             f"🎭 {after.mention} получил роль `{role.name}`")

        for role in removed_roles:
            await log_action(
                "users", before.guild,
                f"🎭 У {after.mention} отобрана роль `{role.name}`")


@tasks.loop(minutes=10)
async def cleanup_inactive():
    now = datetime.utcnow()
    to_delete = []
    for cat_id, data in active_obzvons.items():
        if now - data["timestamp"] > timedelta(hours=1):
            for ch in data["channels"]:
                await ch.delete()
            for role in data["roles"]:
                await role.delete()
            await data["text_channel"].delete()
            await data["category"].delete()
            to_delete.append(cat_id)
    for cat_id in to_delete:
        del active_obzvons[cat_id]


@bot.event
async def on_ready():
    await bot.tree.sync()
    cleanup_inactive.start()
    print(f"Бот {bot.user} запущен")
    print("Система логирования активна!")
    print("Доступные каналы для логов:", list(LOG_CHANNELS.values()))


@bot.command(name="say")
async def say(ctx, *, message):
    """Команда для отправки сообщения от имени бота"""
    try:
        await ctx.message.delete()
    except:
        pass
    await ctx.send(message)


# ВСТАВЬТЕ ВАШ ТОКЕН БОТА ЗДЕСЬ ↓
BOT_TOKEN = "MTMzMzM1MDY4NTQxMjAzNjYzOA.GP2wXi.g4ylhuW3c21eSFd0at4CmGqMkvHpyf1W0HELA0"

if __name__ == "__main__":
    if BOT_TOKEN == "ВАШ_ТОКЕН_БОТА_ЗДЕСЬ":
        print("❌ ОШИБКА: Вставьте ваш токен бота в переменную BOT_TOKEN!")
        print("Замените 'ВАШ_ТОКЕН_БОТА_ЗДЕСЬ' на реальный токен вашего бота Discord")
        exit(1)
    
    bot.run(BOT_TOKEN)

