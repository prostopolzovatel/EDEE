import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional
import re
import random

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

# Токен бота
BOT_TOKEN = "8489477150:AAGaipKgwWfiSgH3IdRyAnyNBXwAE_bknf0"
ADMIN_ID = 8423212939

# Глобальная переменная для ID группы
GROUP_ID = None  # Сюда будет установлен ID группы после добавления бота

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Хранилище заказов и тикетов поддержки
orders: Dict[int, dict] = {}  # Ключ - ID пользователя
support_tickets: Dict[int, dict] = {}  # Ключ - ID тикета (четырехзначный)
user_tickets: Dict[int, int] = {}  # Связь пользователя с его тикетом

# Генератор четырехзначных номеров
def generate_ticket_number() -> int:
    """Генерирует уникальный четырехзначный номер тикета"""
    while True:
        number = random.randint(1000, 9999)
        if number not in support_tickets:
            return number

def generate_order_number() -> int:
    """Генерирует уникальный четырехзначный номер заказа"""
    while True:
        number = random.randint(1000, 9999)
        # Проверяем, не используется ли номер в заказах
        used = False
        for order in orders.values():
            if order.get('order_number') == number:
                used = True
                break
        if not used:
            return number

# Состояния для FSM
class OrderStates(StatesGroup):
    waiting_for_description = State()
    waiting_for_review_link = State()
    waiting_for_order_id_status = State()
    waiting_for_new_status = State()
    waiting_for_payment_confirm = State()

class SupportStates(StatesGroup):
    waiting_for_user_message = State()
    waiting_for_admin_reply = State()

class AdminStates(StatesGroup):
    waiting_for_group_id = State()
    waiting_for_ticket_reply = State()
    waiting_for_user_message = State()
    waiting_for_order_link = State()  # Для ввода ссылки на бота
    waiting_for_order_status = State()  # Для ввода статуса

# Функция для отправки уведомлений в группу
async def send_group_notification(text: str, parse_mode: str = "Markdown"):
    """Отправка уведомления в группу, если она настроена"""
    global GROUP_ID
    if GROUP_ID:
        try:
            await bot.send_message(GROUP_ID, text, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"Ошибка отправки в группу: {e}")

# Функция проверки админа
def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь админом"""
    return user_id == ADMIN_ID

# Функции для работы с тикетами
def get_or_create_ticket(user_id: int, username: str = None) -> dict:
    """Получить существующий тикет или создать новый"""
    if user_id in user_tickets:
        ticket_id = user_tickets[user_id]
        if ticket_id in support_tickets:
            return support_tickets[ticket_id]
    
    # Создаем новый тикет
    ticket_id = generate_ticket_number()
    support_tickets[ticket_id] = {
        'ticket_id': ticket_id,
        'user_id': user_id,
        'username': username,
        'messages': [],
        'status': 'open',  # open, closed
        'created_at': datetime.now().strftime("%d.%m.%Y %H:%M"),
        'updated_at': datetime.now().strftime("%d.%m.%Y %H:%M")
    }
    user_tickets[user_id] = ticket_id
    return support_tickets[ticket_id]

def get_ticket_by_user(user_id: int) -> Optional[dict]:
    """Получить тикет по ID пользователя"""
    if user_id in user_tickets:
        ticket_id = user_tickets[user_id]
        return support_tickets.get(ticket_id)
    return None

def add_message_to_ticket(ticket_id: int, message: str, sender: str):
    """Добавить сообщение в тикет"""
    if ticket_id in support_tickets:
        support_tickets[ticket_id]['messages'].append({
            'text': message,
            'sender': sender,  # 'user' или 'admin'
            'time': datetime.now().strftime("%d.%m.%Y %H:%M")
        })
        support_tickets[ticket_id]['updated_at'] = datetime.now().strftime("%d.%m.%Y %H:%M")

# Функция для завершения заказа
async def complete_order(user_id: int, hosting_paid: bool = False):
    """Завершить заказ и удалить его из активных"""
    if user_id in orders:
        order_num = orders[user_id].get('order_number', 'N/A')
        
        # Отправляем сообщение о завершении
        hosting_text = "с хостингом" if hosting_paid else "без хостинга"
        try:
            await bot.send_message(
                user_id,
                f"✅ **Заказ #{order_num} завершен {hosting_text}!**\n\n"
                f"Спасибо за сотрудничество! 🤝\n"
                f"Если вам понадобится помощь, обращайтесь в поддержку.",
                parse_mode="Markdown"
            )
        except:
            pass
        
        # Сохраняем номер заказа для уведомления
        order_number = orders[user_id]['order_number']
        
        # Удаляем заказ
        del orders[user_id]
        
        # Уведомление админу
        await bot.send_message(
            ADMIN_ID,
            f"✅ Заказ #{order_number} завершен {hosting_text} и удален из списка активных."
        )
        
        # Уведомление в группу
        group_text = f"✅ Заказ #{order_number} завершен {hosting_text}"
        await send_group_notification(group_text)
        
        return True
    return False

# Клавиатуры для пользователей (только самые необходимые)
def get_main_keyboard(user_id: int):
    """Главное меню для пользователей"""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="📝 Сделать заказ", callback_data="new_order"))
    builder.add(InlineKeyboardButton(text="📊 Мой заказ", callback_data="my_order"))
    builder.add(InlineKeyboardButton(text="📞 Поддержка", callback_data="support"))
    builder.adjust(1)
    return builder.as_markup()

def get_support_keyboard(user_id: int):
    """Клавиатура для поддержки"""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="✏️ Написать сообщение", callback_data="support_write"))
    builder.add(InlineKeyboardButton(text="📜 История", callback_data="support_history"))
    builder.add(InlineKeyboardButton(text="❌ Закрыть тикет", callback_data="support_close"))
    builder.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main"))
    builder.adjust(1)
    return builder.as_markup()

# Обработчики команд (публичные)
@dp.message(CommandStart())
async def command_start_handler(message: types.Message) -> None:
    """Обработчик команды /start"""
    user = message.from_user
    welcome_text = (
        f"👋 Добро пожаловать, {user.first_name}!\n\n"
        "🤖 Я бот для заказа разработки Telegram ботов.\n\n"
        "📌 **Услуги:**\n"
        "• Разработка бота: **100 ⭐**\n"
        "• Хостинг (месяц): **+100 ⭐** (не обязательно)\n"
        "• Поддержка 24/7\n\n"
        "Выберите действие в меню ниже:"
    )
    
    await message.answer(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(user.id)
    )

# ==================== АДМИН КОМАНДЫ (ТОЛЬКО ДЛЯ АДМИНА) ====================

@dp.message(Command("admin"))
async def cmd_admin_help(message: types.Message):
    """Главная админ команда - показывает все доступные команды"""
    if not is_admin(message.from_user.id):
        return  # Полное игнорирование
    
    help_text = (
        "👑 **АДМИН ПАНЕЛЬ**\n\n"
        "📋 **УПРАВЛЕНИЕ ЗАКАЗАМИ:**\n"
        "• /orders - список всех заказов\n"
        "• /order [номер] - просмотр заказа\n"
        "• /status [номер] [статус] - изменить статус\n"
        "• /link [номер] - отправить ссылку на бота\n"
        "• /pay_bot [номер] - подтвердить оплату бота\n"
        "• /pay_hosting [номер] - подтвердить оплату хостинга\n\n"
        "📞 **ПОДДЕРЖКА:**\n"
        "• /tickets - список открытых тикетов\n"
        "• /ticket [ID] - просмотр тикета\n"
        "• /reply [ID] [текст] - ответить в тикет\n"
        "• /close [ID] - закрыть тикет\n"
        "• /msg [ID] [текст] - написать пользователю\n\n"
        "⚙️ **НАСТРОЙКИ:**\n"
        "• /group [ID] - установить группу для уведомлений\n"
        "• /group_off - отключить уведомления\n"
        "• /group_status - статус группы"
    )
    
    await message.answer(help_text, parse_mode="Markdown")

# ==================== УПРАВЛЕНИЕ ЗАКАЗАМИ ====================

@dp.message(Command("orders"))
async def cmd_orders(message: types.Message):
    """Список всех заказов"""
    if not is_admin(message.from_user.id):
        return
    
    if not orders:
        await message.answer("📋 Нет активных заказов.")
        return
    
    text = "📋 **АКТИВНЫЕ ЗАКАЗЫ:**\n\n"
    for user_id, order in orders.items():
        status_emoji = {
            "Принят в работу": "📥",
            "В разработке": "💻",
            "Готов к просмотру": "👀",
        }.get(order['status'], "📋")
        
        payment_status = ""
        if order.get('bot_paid'):
            payment_status += "💰"
        if order.get('hosting_paid'):
            payment_status += "🌐"
        
        text += f"{status_emoji} **#{order['order_number']}** {payment_status}\n"
        text += f"👤 @{order['username']}\n"
        text += f"📊 {order['status']}\n"
        text += f"📅 {order['date']}\n"
        text += "-" * 20 + "\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("order"))
async def cmd_order(message: types.Message):
    """Просмотр конкретного заказа"""
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) != 2:
        await message.answer("❌ Использование: /order [номер заказа]")
        return
    
    try:
        order_number = int(args[1])
        
        # Ищем заказ по номеру
        target_user_id = None
        for user_id, order in orders.items():
            if order.get('order_number') == order_number:
                target_user_id = user_id
                break
        
        if not target_user_id:
            await message.answer(f"❌ Заказ #{order_number} не найден.")
            return
        
        order = orders[target_user_id]
        
        text = (
            f"📋 **ЗАКАЗ #{order_number}**\n\n"
            f"👤 **Пользователь:** @{order['username']} (ID: {target_user_id})\n"
            f"📊 **Статус:** {order['status']}\n"
            f"📅 **Дата:** {order['date']}\n\n"
            f"📝 **Описание:**\n{order['description']}\n\n"
        )
        
        if order.get('review_link'):
            text += f"🔗 **Ссылка:** {order['review_link']}\n\n"
        
        text += f"💰 **Оплата:**\n"
        text += f"• Бот: {'✅' if order.get('bot_paid') else '❌'}\n"
        text += f"• Хостинг: {'✅' if order.get('hosting_paid') else '❌'}\n"
        
        await message.answer(text, parse_mode="Markdown")
        
    except ValueError:
        await message.answer("❌ Неверный формат номера заказа.")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Изменить статус заказа"""
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ Использование: /status [номер заказа] [статус]")
        await message.answer("Доступные статусы: Принят в работу, В разработке, Готов к просмотру")
        return
    
    try:
        order_number = int(args[1])
        new_status = args[2]
        
        # Ищем заказ по номеру
        target_user_id = None
        for user_id, order in orders.items():
            if order.get('order_number') == order_number:
                target_user_id = user_id
                break
        
        if not target_user_id:
            await message.answer(f"❌ Заказ #{order_number} не найден.")
            return
        
        valid_statuses = ["Принят в работу", "В разработке", "Готов к просмотру"]
        if new_status not in valid_statuses:
            await message.answer(f"❌ Неверный статус. Доступны: {', '.join(valid_statuses)}")
            return
        
        orders[target_user_id]['status'] = new_status
        
        # Уведомление пользователю
        try:
            await bot.send_message(
                target_user_id,
                f"📊 **Статус вашего заказа #{order_number} изменен!**\n\n"
                f"Новый статус: **{new_status}**",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить пользователя: {e}")
        
        await message.answer(f"✅ Статус заказа #{order_number} изменен на: {new_status}")
        
        # Уведомление в группу
        await send_group_notification(f"📊 Статус заказа #{order_number} изменен на: {new_status}")
        
    except ValueError:
        await message.answer("❌ Неверный формат номера заказа.")

@dp.message(Command("link"))
async def cmd_link(message: types.Message, state: FSMContext):
    """Отправить ссылку на бота"""
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) != 2:
        await message.answer("❌ Использование: /link [номер заказа]")
        return
    
    try:
        order_number = int(args[1])
        
        # Ищем заказ по номеру
        target_user_id = None
        for user_id, order in orders.items():
            if order.get('order_number') == order_number:
                target_user_id = user_id
                break
        
        if not target_user_id:
            await message.answer(f"❌ Заказ #{order_number} не найден.")
            return
        
        await state.update_data(link_order_number=order_number, link_user_id=target_user_id)
        await message.answer(f"🔗 Отправьте ссылку на готового бота для заказа #{order_number}:")
        await state.set_state(AdminStates.waiting_for_order_link)
        
    except ValueError:
        await message.answer("❌ Неверный формат номера заказа.")

@dp.message(AdminStates.waiting_for_order_link)
async def process_admin_link(message: types.Message, state: FSMContext):
    """Обработка ссылки от админа"""
    if not is_admin(message.from_user.id):
        return
    
    data = await state.get_data()
    user_id = data.get('link_user_id')
    order_number = data.get('link_order_number')
    link = message.text
    
    if user_id in orders:
        orders[user_id]['review_link'] = link
        orders[user_id]['status'] = 'Готов к просмотру'
        
        try:
            await bot.send_message(
                user_id,
                f"🎉 **Ваш бот готов к просмотру!**\n\n"
                f"📋 **Номер заказа:** #{order_number}\n"
                f"🔗 **Ссылка:** {link}\n\n"
                f"📊 **Статус:** Готов к просмотру\n\n"
                f"💰 **Для получения бота:**\n"
                f"1. Проверьте работу бота\n"
                f"2. Если всё устраивает, нажмите кнопку '💰 Оплатить бота (100⭐)'\n"
                f"3. После оплаты вы сможете приобрести хостинг\n\n"
                f"❗️ Хостинг оплачивается отдельно: +100⭐ в месяц",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(text="💰 Оплатить бота (100⭐)", callback_data="pay_bot")
                    ]]
                )
            )
            
            await message.answer(f"✅ Ссылка отправлена пользователю #{order_number}")
            await send_group_notification(f"🔗 Ссылка на бота отправлена для заказа #{order_number}")
            
        except Exception as e:
            await message.answer(f"❌ Ошибка при отправке: {e}")
    else:
        await message.answer("❌ Заказ не найден")
    
    await state.clear()

@dp.message(Command("pay_bot"))
async def cmd_pay_bot(message: types.Message):
    """Подтвердить оплату бота"""
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) != 2:
        await message.answer("❌ Использование: /pay_bot [номер заказа]")
        return
    
    try:
        order_number = int(args[1])
        
        # Ищем заказ по номеру
        target_user_id = None
        for user_id, order in orders.items():
            if order.get('order_number') == order_number:
                target_user_id = user_id
                break
        
        if not target_user_id:
            await message.answer(f"❌ Заказ #{order_number} не найден.")
            return
        
        if target_user_id in orders:
            orders[target_user_id]['bot_paid'] = True
            
            try:
                await bot.send_message(
                    target_user_id,
                    f"✅ **Оплата бота #{order_number} подтверждена!**\n\n"
                    f"Бот будет передан вам.\n\n"
                    f"Теперь вы можете:\n"
                    f"• Оплатить хостинг (100⭐/месяц)\n"
                    f"• Отказаться от хостинга и завершить заказ",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="🌐 Оплатить хостинг (100⭐)", callback_data="pay_hosting")],
                            [InlineKeyboardButton(text="❌ Отказаться от хостинга", callback_data="decline_hosting")]
                        ]
                    )
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя: {e}")
            
            await message.answer(f"✅ Оплата бота #{order_number} подтверждена")
            await send_group_notification(f"💰 Оплата бота #{order_number} подтверждена")
        else:
            await message.answer("❌ Заказ не найден")
        
    except ValueError:
        await message.answer("❌ Неверный формат номера заказа.")

@dp.message(Command("pay_hosting"))
async def cmd_pay_hosting(message: types.Message):
    """Подтвердить оплату хостинга"""
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) != 2:
        await message.answer("❌ Использование: /pay_hosting [номер заказа]")
        return
    
    try:
        order_number = int(args[1])
        
        # Ищем заказ по номеру
        target_user_id = None
        for user_id, order in orders.items():
            if order.get('order_number') == order_number:
                target_user_id = user_id
                break
        
        if not target_user_id:
            await message.answer(f"❌ Заказ #{order_number} не найден.")
            return
        
        if target_user_id in orders:
            orders[target_user_id]['hosting_paid'] = True
            await complete_order(target_user_id, hosting_paid=True)
            await message.answer(f"✅ Оплата хостинга #{order_number} подтверждена, заказ завершен")
            await send_group_notification(f"🌐 Оплата хостинга #{order_number} подтверждена, заказ завершен")
        else:
            await message.answer("❌ Заказ не найден")
        
    except ValueError:
        await message.answer("❌ Неверный формат номера заказа.")

# ==================== УПРАВЛЕНИЕ ПОДДЕРЖКОЙ ====================

@dp.message(Command("tickets"))
async def cmd_tickets(message: types.Message):
    """Список открытых тикетов"""
    if not is_admin(message.from_user.id):
        return
    
    if not support_tickets:
        await message.answer("📭 Нет тикетов.")
        return
    
    text = "📞 **ОТКРЫТЫЕ ТИКЕТЫ:**\n\n"
    for ticket_id, ticket in support_tickets.items():
        if ticket['status'] == 'open':
            text += f"🎫 **#{ticket_id}** - @{ticket.get('username', 'Неизвестно')}\n"
            text += f"💬 Сообщений: {len(ticket['messages'])}\n"
            text += f"🕐 {ticket['updated_at']}\n\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(Command("ticket"))
async def cmd_ticket(message: types.Message):
    """Просмотр конкретного тикета"""
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) != 2:
        await message.answer("❌ Использование: /ticket [ID тикета]")
        return
    
    try:
        ticket_id = int(args[1])
        
        if ticket_id not in support_tickets:
            await message.answer(f"❌ Тикет #{ticket_id} не найден.")
            return
        
        ticket = support_tickets[ticket_id]
        
        text = (
            f"📞 **ТИКЕТ #{ticket_id}**\n\n"
            f"👤 **Пользователь:** @{ticket.get('username', 'Неизвестно')} (ID: {ticket['user_id']})\n"
            f"📊 **Статус:** {'🟢 Открыт' if ticket['status'] == 'open' else '🔴 Закрыт'}\n"
            f"📅 **Создан:** {ticket['created_at']}\n"
            f"🔄 **Обновлен:** {ticket['updated_at']}\n\n"
            f"**ИСТОРИЯ ПЕРЕПИСКИ:**\n\n"
        )
        
        if ticket['messages']:
            for msg in ticket['messages']:
                sender = "👤 Пользователь" if msg['sender'] == 'user' else "👑 Админ"
                text += f"{sender} [{msg['time']}]:\n{msg['text']}\n\n"
        else:
            text += "Нет сообщений\n"
        
        await message.answer(text, parse_mode="Markdown")
        
    except ValueError:
        await message.answer("❌ Неверный формат ID тикета.")

@dp.message(Command("reply"))
async def cmd_reply(message: types.Message, state: FSMContext):
    """Ответить в тикет"""
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ Использование: /reply [ID тикета] [текст ответа]")
        return
    
    try:
        ticket_id = int(args[1])
        reply_text = args[2]
        
        if ticket_id not in support_tickets:
            await message.answer(f"❌ Тикет #{ticket_id} не найден.")
            return
        
        ticket = support_tickets[ticket_id]
        user_id = ticket['user_id']
        
        # Добавляем сообщение в тикет
        add_message_to_ticket(ticket_id, reply_text, 'admin')
        
        try:
            await bot.send_message(
                user_id,
                f"📨 **Ответ от поддержки в тикет #{ticket_id}:**\n\n{reply_text}",
                parse_mode="Markdown"
            )
            
            await message.answer(f"✅ Ответ отправлен в тикет #{ticket_id}")
            await send_group_notification(f"📞 Админ ответил в тикет #{ticket_id}")
            
        except Exception as e:
            await message.answer(f"❌ Ошибка при отправке: {e}")
        
    except ValueError:
        await message.answer("❌ Неверный формат ID тикета.")

@dp.message(Command("close"))
async def cmd_close(message: types.Message):
    """Закрыть тикет"""
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) != 2:
        await message.answer("❌ Использование: /close [ID тикета]")
        return
    
    try:
        ticket_id = int(args[1])
        
        if ticket_id not in support_tickets:
            await message.answer(f"❌ Тикет #{ticket_id} не найден.")
            return
        
        ticket = support_tickets[ticket_id]
        ticket['status'] = 'closed'
        user_id = ticket['user_id']
        
        try:
            await bot.send_message(
                user_id,
                f"📞 **Тикет #{ticket_id} закрыт администратором.**\n\n"
                "Если у вас остались вопросы, вы можете создать новый тикет.",
                parse_mode="Markdown"
            )
        except:
            pass
        
        await message.answer(f"✅ Тикет #{ticket_id} закрыт")
        await send_group_notification(f"📞 Админ закрыл тикет #{ticket_id}")
        
    except ValueError:
        await message.answer("❌ Неверный формат ID тикета.")

@dp.message(Command("msg"))
async def cmd_msg(message: types.Message, state: FSMContext):
    """Написать сообщение пользователю"""
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ Использование: /msg [ID пользователя] [текст сообщения]")
        return
    
    try:
        user_id = int(args[1])
        msg_text = args[2]
        
        try:
            await bot.send_message(
                user_id,
                f"📨 **Сообщение от администратора:**\n\n{msg_text}",
                parse_mode="Markdown"
            )
            
            await message.answer(f"✅ Сообщение отправлено пользователю #{user_id}")
            await send_group_notification(f"📞 Админ отправил сообщение пользователю #{user_id}")
            
        except Exception as e:
            await message.answer(f"❌ Ошибка при отправке: {e}")
        
    except ValueError:
        await message.answer("❌ Неверный формат ID пользователя.")

# ==================== НАСТРОЙКИ ГРУППЫ ====================

@dp.message(Command("group"))
async def cmd_group(message: types.Message):
    """Установить ID группы"""
    global GROUP_ID
    if not is_admin(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) != 2:
        await message.answer("❌ Использование: /group [ID группы]")
        return
    
    try:
        group_id = int(args[1])
        GROUP_ID = group_id
        await message.answer(f"✅ ID группы установлен: {GROUP_ID}")
        await message.answer("Теперь уведомления будут приходить в эту группу.")
    except ValueError:
        await message.answer("❌ Неверный формат ID группы.")

@dp.message(Command("group_off"))
async def cmd_group_off(message: types.Message):
    """Отключить уведомления в группу"""
    global GROUP_ID
    if not is_admin(message.from_user.id):
        return
    
    GROUP_ID = None
    await message.answer("✅ Уведомления в группу отключены.")

@dp.message(Command("group_status"))
async def cmd_group_status(message: types.Message):
    """Статус группы"""
    if not is_admin(message.from_user.id):
        return
    
    status = "✅ Настроена" if GROUP_ID else "❌ Не настроена"
    await message.answer(
        f"📊 **Статус группы**\n\n"
        f"Текущий статус: {status}\n"
        f"ID группы: {GROUP_ID or 'Не указан'}",
        parse_mode="Markdown"
    )

@dp.message(Command("groupid"))
async def command_groupid_handler(message: types.Message) -> None:
    """Получение ID группы (работает только в группе)"""
    if not is_admin(message.from_user.id):
        return
    
    if message.chat.type in ["group", "supergroup"]:
        await message.answer(f"✅ ID этой группы: `{message.chat.id}`", parse_mode="Markdown")
    else:
        await message.answer("❌ Эта команда работает только в группах.")

# ==================== ОБРАБОТЧИКИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ====================

@dp.callback_query(F.data == "new_order")
async def process_new_order(callback: CallbackQuery, state: FSMContext):
    """Создание нового заказа"""
    try:
        await callback.message.edit_text(
            "📝 Пожалуйста, опишите подробно техническое задание для вашего бота:\n\n"
            "Укажите:\n"
            "• Какие функции должен выполнять бот\n"
            "• Примерный дизайн/оформление\n"
            "• Сроки разработки\n"
            "• Дополнительные пожелания",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="back_to_main")]]
            )
        )
        await state.set_state(OrderStates.waiting_for_description)
    except TelegramBadRequest:
        await callback.message.answer(
            "📝 Опишите техническое задание:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="back_to_main")]]
            )
        )
        await state.set_state(OrderStates.waiting_for_description)
    await callback.answer()

@dp.callback_query(F.data == "my_order")
async def process_my_order(callback: CallbackQuery):
    """Просмотр своего заказа"""
    user_id = callback.from_user.id
    
    if user_id in orders:
        order = orders[user_id]
        order_info = (
            f"📋 **Информация о вашем заказе**\n\n"
            f"📋 **Номер заказа:** #{order['order_number']}\n"
            f"📝 **Описание:**\n{order['description']}\n\n"
            f"📊 **Статус:** {order['status']}\n"
            f"📅 **Дата заказа:** {order['date']}\n\n"
        )
        
        if order.get('review_link'):
            order_info += f"🔗 **Ссылка на бота:** {order['review_link']}\n\n"
        
        order_info += f"💰 **Статус оплаты:**\n"
        order_info += f"• Бот: {'✅' if order.get('bot_paid') else '❌'} 100⭐\n"
        order_info += f"• Хостинг: {'✅' if order.get('hosting_paid') else '❌'} 100⭐\n"
        
        # Создаем клавиатуру с кнопками
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(text="🔄 Обновить", callback_data="my_order"))
        
        if order.get('review_link') and not order.get('bot_paid'):
            builder.add(InlineKeyboardButton(text="💰 Оплатить бота (100⭐)", callback_data="pay_bot"))
        
        if order.get('bot_paid') and not order.get('hosting_paid'):
            builder.add(InlineKeyboardButton(text="🌐 Оплатить хостинг (100⭐)", callback_data="pay_hosting"))
            builder.add(InlineKeyboardButton(text="❌ Без хостинга", callback_data="decline_hosting"))
        
        builder.add(InlineKeyboardButton(text="📞 Поддержка", callback_data="support"))
        builder.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main"))
        builder.adjust(1)
        
        try:
            await callback.message.edit_text(
                order_info,
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
        except TelegramBadRequest:
            await callback.message.answer(
                order_info,
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
    else:
        text = "❌ У вас пока нет активных заказов.\nНажмите '📝 Сделать заказ', чтобы оформить заявку."
        try:
            await callback.message.edit_text(text, reply_markup=get_main_keyboard(user_id))
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=get_main_keyboard(user_id))
    await callback.answer()

@dp.callback_query(F.data == "pay_bot")
async def process_pay_bot(callback: CallbackQuery):
    """Оплата бота"""
    user_id = callback.from_user.id
    
    if user_id not in orders:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    order = orders[user_id]
    
    if order.get('bot_paid'):
        await callback.answer("✅ Бот уже оплачен", show_alert=True)
        return
    
    if not order.get('review_link'):
        await callback.answer("❌ Ссылка на бота еще не готова", show_alert=True)
        return
    
    # Создаем ссылку на оплату звездами
    invoice_link = f"tg://stars?amount=100&title=Оплата%20бота&description=Оплата%20бота%20#{order['order_number']}"
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="💫 Оплатить 100⭐", url=invoice_link))
    builder.add(InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"user_bot_paid"))
    builder.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="my_order"))
    builder.adjust(1)
    
    text = (
        f"💰 **Оплата бота #{order['order_number']}**\n\n"
        f"Сумма: **100 ⭐**\n\n"
        f"1. Нажмите кнопку 'Оплатить'\n"
        f"2. Подтвердите оплату в Telegram\n"
        f"3. После оплаты нажмите 'Я оплатил'"
    )
    
    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())
    except TelegramBadRequest:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "pay_hosting")
async def process_pay_hosting(callback: CallbackQuery):
    """Оплата хостинга"""
    user_id = callback.from_user.id
    
    if user_id not in orders:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    order = orders[user_id]
    
    if not order.get('bot_paid'):
        await callback.answer("❌ Сначала оплатите бота", show_alert=True)
        return
    
    if order.get('hosting_paid'):
        await callback.answer("✅ Хостинг уже оплачен", show_alert=True)
        return
    
    # Создаем ссылку на оплату звездами
    invoice_link = f"tg://stars?amount=100&title=Оплата%20хостинга&description=Хостинг%20для%20бота%20#{order['order_number']}"
    
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="💫 Оплатить 100⭐", url=invoice_link))
    builder.add(InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"user_hosting_paid"))
    builder.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="my_order"))
    builder.adjust(1)
    
    text = (
        f"🌐 **Оплата хостинга для бота #{order['order_number']}**\n\n"
        f"Сумма: **100 ⭐** (месяц)\n\n"
        f"1. Нажмите кнопку 'Оплатить'\n"
        f"2. Подтвердите оплату в Telegram\n"
        f"3. После оплаты нажмите 'Я оплатил'"
    )
    
    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())
    except TelegramBadRequest:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "decline_hosting")
async def process_decline_hosting(callback: CallbackQuery):
    """Отказ от хостинга"""
    user_id = callback.from_user.id
    
    if user_id not in orders:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    order = orders[user_id]
    
    if not order.get('bot_paid'):
        await callback.answer("❌ Сначала оплатите бота", show_alert=True)
        return
    
    # Завершаем заказ без хостинга
    await complete_order(user_id, hosting_paid=False)
    
    text = "✅ Заказ завершен без хостинга. Спасибо за сотрудничество!"
    
    try:
        await callback.message.edit_text(text, reply_markup=get_main_keyboard(user_id))
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=get_main_keyboard(user_id))
    await callback.answer()

@dp.callback_query(F.data == "user_bot_paid")
async def process_user_bot_paid(callback: CallbackQuery):
    """Пользователь нажал 'Я оплатил' за бота"""
    user_id = callback.from_user.id
    
    if user_id not in orders:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    order_num = orders[user_id]['order_number']
    
    text = f"✅ Запрос на подтверждение оплаты бота #{order_num} отправлен администратору!"
    
    try:
        await callback.message.edit_text(text, reply_markup=get_main_keyboard(user_id))
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=get_main_keyboard(user_id))
    
    # Уведомление админу
    admin_text = (
        f"💰 **ЗАПРОС НА ПОДТВЕРЖДЕНИЕ ОПЛАТЫ БОТА!**\n\n"
        f"👤 Пользователь: @{callback.from_user.username or 'Неизвестно'} (ID: {user_id})\n"
        f"📦 Заказ: #{order_num}\n"
        f"💫 Сумма: 100 ⭐"
    )
    
    await bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")
    await send_group_notification(f"💰 Запрос на оплату бота #{order_num}")
    await callback.answer("Запрос отправлен!")

@dp.callback_query(F.data == "user_hosting_paid")
async def process_user_hosting_paid(callback: CallbackQuery):
    """Пользователь нажал 'Я оплатил' за хостинг"""
    user_id = callback.from_user.id
    
    if user_id not in orders:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    order_num = orders[user_id]['order_number']
    
    text = f"✅ Запрос на подтверждение оплаты хостинга #{order_num} отправлен администратору!"
    
    try:
        await callback.message.edit_text(text, reply_markup=get_main_keyboard(user_id))
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=get_main_keyboard(user_id))
    
    # Уведомление админу
    admin_text = (
        f"🌐 **ЗАПРОС НА ПОДТВЕРЖДЕНИЕ ОПЛАТЫ ХОСТИНГА!**\n\n"
        f"👤 Пользователь: @{callback.from_user.username or 'Неизвестно'} (ID: {user_id})\n"
        f"📦 Заказ: #{order_num}\n"
        f"💫 Сумма: 100 ⭐"
    )
    
    await bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")
    await send_group_notification(f"🌐 Запрос на оплату хостинга #{order_num}")
    await callback.answer("Запрос отправлен!")

@dp.callback_query(F.data == "support")
async def process_support(callback: CallbackQuery):
    """Меню поддержки"""
    user_id = callback.from_user.id
    
    ticket = get_ticket_by_user(user_id)
    
    text = "📞 **Служба поддержки**\n\n"
    
    if ticket:
        if ticket['status'] == 'open':
            text += f"У вас есть открытый тикет **#{ticket['ticket_id']}**."
        else:
            text += f"Ваш последний тикет **#{ticket['ticket_id']}** закрыт."
    else:
        text += "Здесь вы можете создать тикет и задать вопросы администратору."
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_support_keyboard(user_id)
        )
    except TelegramBadRequest:
        await callback.message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=get_support_keyboard(user_id)
        )
    await callback.answer()

@dp.callback_query(F.data == "support_write")
async def process_support_write(callback: CallbackQuery, state: FSMContext):
    """Написать сообщение в поддержку"""
    user_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.full_name
    
    # Создаем или получаем тикет
    ticket = get_or_create_ticket(user_id, username)
    ticket['status'] = 'open'
    
    text = (
        f"✏️ **Тикет #{ticket['ticket_id']}**\n\n"
        "Напишите ваше сообщение. Администратор ответит вам в ближайшее время."
    )
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="support")]]
            )
        )
    except TelegramBadRequest:
        await callback.message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="support")]]
            )
        )
    
    await state.set_state(SupportStates.waiting_for_user_message)
    await callback.answer()

@dp.callback_query(F.data == "support_history")
async def process_support_history(callback: CallbackQuery):
    """Просмотр истории переписки"""
    user_id = callback.from_user.id
    ticket = get_ticket_by_user(user_id)
    
    if not ticket:
        text = "📭 У вас нет истории переписки."
    else:
        text = f"📜 **История тикета #{ticket['ticket_id']}**\n\n"
        text += f"📊 Статус: {'🟢 Открыт' if ticket['status'] == 'open' else '🔴 Закрыт'}\n\n"
        
        if ticket['messages']:
            for msg in ticket['messages']:
                sender = "👤 Вы" if msg['sender'] == 'user' else "👑 Админ"
                text += f"{sender} [{msg['time']}]:\n{msg['text']}\n\n"
        else:
            text += "Нет сообщений\n"
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_support_keyboard(user_id)
        )
    except TelegramBadRequest:
        await callback.message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=get_support_keyboard(user_id)
        )
    await callback.answer()

@dp.callback_query(F.data == "support_close")
async def process_support_close(callback: CallbackQuery):
    """Закрытие тикета пользователем"""
    user_id = callback.from_user.id
    ticket = get_ticket_by_user(user_id)
    
    if ticket:
        ticket['status'] = 'closed'
        text = f"✅ Тикет #{ticket['ticket_id']} закрыт."
        
        # Уведомление админу
        await bot.send_message(
            ADMIN_ID,
            f"📞 Пользователь @{callback.from_user.username or 'Неизвестно'} закрыл тикет #{ticket['ticket_id']}."
        )
    else:
        text = "❌ Тикет не найден."
    
    try:
        await callback.message.edit_text(text, reply_markup=get_main_keyboard(user_id))
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=get_main_keyboard(user_id))
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def process_back_to_main(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await state.clear()
    text = "Главное меню:"
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_main_keyboard(callback.from_user.id)
        )
    except TelegramBadRequest:
        await callback.message.answer(
            text,
            reply_markup=get_main_keyboard(callback.from_user.id)
        )
    await callback.answer()

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================

@dp.message(OrderStates.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    """Обработка полученного ТЗ"""
    user_id = message.from_user.id
    description = message.text
    
    # Генерируем номер заказа
    order_number = generate_order_number()
    
    orders[user_id] = {
        'order_number': order_number,
        'description': description,
        'status': 'Принят в работу',
        'date': datetime.now().strftime("%d.%m.%Y %H:%M"),
        'username': message.from_user.username or message.from_user.full_name,
        'bot_paid': False,
        'hosting_paid': False
    }
    
    await message.answer(
        f"✅ Ваше техническое задание принято!\n\n"
        f"📋 **Номер заказа:** #{order_number}\n"
        f"📊 **Статус:** Принят в работу",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(user_id)
    )
    
    # Уведомление админу
    admin_text = (
        f"🆕 **НОВЫЙ ЗАКАЗ!**\n\n"
        f"📋 **Номер заказа:** #{order_number}\n"
        f"👤 **Пользователь:** @{message.from_user.username or 'Неизвестно'} (ID: {user_id})\n"
        f"📝 **ТЗ:**\n{description}"
    )
    
    await bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")
    await send_group_notification(f"🆕 Новый заказ #{order_number}")
    
    await state.clear()

@dp.message(SupportStates.waiting_for_user_message)
async def process_user_support_message(message: types.Message, state: FSMContext):
    """Обработка сообщения пользователя в поддержку"""
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name
    
    # Получаем или создаем тикет
    ticket = get_or_create_ticket(user_id, username)
    ticket['status'] = 'open'
    
    # Добавляем сообщение в тикет
    add_message_to_ticket(ticket['ticket_id'], message.text, 'user')
    
    await message.answer(
        f"✅ Ваше сообщение в тикет #{ticket['ticket_id']} отправлено.",
        reply_markup=get_main_keyboard(user_id)
    )
    
    # Уведомление админу
    admin_text = (
        f"📞 **НОВОЕ СООБЩЕНИЕ В ПОДДЕРЖКУ!**\n\n"
        f"🎫 **Тикет:** #{ticket['ticket_id']}\n"
        f"👤 **Пользователь:** @{username} (ID: {user_id})\n"
        f"📝 **Сообщение:**\n{message.text}"
    )
    
    await bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")
    await send_group_notification(f"📞 Новое сообщение в тикет #{ticket['ticket_id']}")
    
    await state.clear()

# ==================== ЗАПУСК ====================

async def main():
    """Запуск бота"""
    logger.info("Бот запущен и готов к работе!")
    logger.info(f"Админ ID: {ADMIN_ID}")
    logger.info("Для входа в админ панель используйте команду /admin")
    
    # Устанавливаем команды
    commands = [
        types.BotCommand(command="start", description="Запустить бота"),
        types.BotCommand(command="admin", description="Админ панель (только для админа)"),
    ]
    await bot.set_my_commands(commands)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
