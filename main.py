import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from recipes import RECIPES, calculate_recipe_cost, STORE_PRICES

logging.basicConfig(level=logging.INFO)
bot = Bot(token=config.TG_BOT_API_KEY)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Локальное runtime-хранилище
USER_DATA = {}

# Состояния FSM (машина состояний)
class BotStates(StatesGroup):
    waiting_for_diet = State()
    waiting_for_gender = State()
    waiting_for_weight = State()
    waiting_for_height = State()
    waiting_for_age = State()
    waiting_for_activity = State()
    waiting_for_budget_period = State()
    waiting_for_budget_amount = State()
    waiting_for_store = State()
    
    # Сбор продуктов
    waiting_for_bulk = State()
    waiting_for_qty = State()
    waiting_for_exp = State()

# Кнопки главного меню
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📦 Наличие продуктов"), KeyboardButton(text="➕ Добавить продукт")],
        [KeyboardButton(text="📝 Закуп чек лист"), KeyboardButton(text="🍳 Предложить рецепт")]
    ],
    resize_keyboard=True
)

def get_category(name: str) -> str:
    name = name.lower().strip()
    categories = {
        "Мясо и птица": ["мясо", "курица", "индейка", "говядина", "свинина", "фарш", "субпродукты"],
        "Рыба и морепродукты": ["рыба", "лосось", "треска", "креветки", "морепродукты"],
        "Молочные продукты": ["молоко", "творог", "сыр", "йогурт", "сметана", "сливки"],
        "Овощи и зелень": ["огурец", "огурцы", "помидор", "помидоры", "зелень", "укроп", "петрушка", "салат"],
        "Фрукты и ягоды": ["яблоко", "банан", "ягода", "клубника", "малина", "черника", "ягоды"],
        "Бакалея": ["крупа", "рис", "гречка", "макароны", "мука", "сахар", "соль", "масло", "овсянка", "вода"],
        "Соусы": ["майонез", "маргарин"]
    }
    for cat, keywords in categories.items():
        if any(k in name for k in keywords):
            return cat
    return "Другое"

def needs_expiration(name: str) -> bool:
    name = name.lower().strip()
    keywords = [
        "мясо", "птица", "курица", "индейка", "говядина", "свинина", "субпродукты", 
        "рыба", "морепродукты", "креветки", "молоко", "творог", "сыр", "йогурт", 
        "сметана", "яйца", "огурец", "огурцы", "помидор", "помидоры", "ягода", 
        "ягоды", "зелень", "майонез", "маргарин"
    ]
    return any(k in name for k in keywords)

def guess_unit(name: str) -> str:
    name = name.lower().strip()
    if any(k in name for k in ["молоко", "вода", "сливки", "масло"]):
        return "мл"
    if any(k in name for k in ["яйца", "яблоко", "огурец", "помидор", "банан"]):
        return "шт"
    return "грамм"

def calculate_kbzhu(gender: str, weight: float, height: float, age: int, activity_factor: float):
    # Уравнение Миффлина - Сан Жеора
    if gender == "м":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
        
    calories = int(bmr * activity_factor)
    
    # Распределение: Б - 30%, Ж - 30%, У - 40% от калорийности
    proteins = int((calories * 0.3) / 4)
    fats = int((calories * 0.3) / 9)
    carbs = int((calories * 0.4) / 4)
    
    return calories, proteins, fats, carbs

# --- СТАРТ И ИНИЦИАЛЬНЫЙ ОПРОС ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="🥦 Веган", callback_data="diet_веган")
    builder.button(text="🥛 Без лактозы", callback_data="diet_непереносимость лактозы")
    builder.button(text="🚫 Аллергия на продукты", callback_data="diet_аллергия")
    builder.button(text="🩸 Диабет", callback_data="diet_диабет")
    builder.button(text="🍽️ Нет ограничений", callback_data="diet_нет")
    builder.adjust(1)
    
    await message.answer(
        "Привет! Давайте настроим ваш профиль питания.\n"
        "Выберите ваши особенности диеты:",
        reply_markup=builder.as_markup()
    )
    await state.set_state(BotStates.waiting_for_diet)

@dp.callback_query(BotStates.waiting_for_diet, F.data.startswith("diet_"))
async def process_diet(callback: types.CallbackQuery, state: FSMContext):
    diet = callback.data.split("_")[1]
    await state.update_data(diet=diet)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="Мужской ♂️", callback_data="gender_м")
    builder.button(text="Женский ♀️", callback_data="gender_ж")
    
    await callback.message.answer("Выберите ваш пол для расчета калорий:", reply_markup=builder.as_markup())
    await state.set_state(BotStates.waiting_for_gender)
    await callback.answer()

@dp.callback_query(BotStates.waiting_for_gender, F.data.startswith("gender_"))
async def process_gender(callback: types.CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]
    await state.update_data(gender=gender)
    
    await callback.message.answer("Введите ваш вес в кг (например: 75):")
    await state.set_state(BotStates.waiting_for_weight)
    await callback.answer()

@dp.message(BotStates.waiting_for_weight)
async def process_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.strip())
        await state.update_data(weight=weight)
        await message.answer("Укажите ваш рост в см (например: 176):")
        await state.set_state(BotStates.waiting_for_height)
    except ValueError:
        await message.answer("Введите корректное число (например: 75):")

@dp.message(BotStates.waiting_for_height)
async def process_height(message: types.Message, state: FSMContext):
    try:
        height = float(message.text.strip())
        await state.update_data(height=height)
        await message.answer("Укажите ваш возраст (полных лет, например: 25):")
        await state.set_state(BotStates.waiting_for_age)
    except ValueError:
        await message.answer("Введите корректное число (например: 176):")

@dp.message(BotStates.waiting_for_age)
async def process_age(message: types.Message, state: FSMContext):
    try:
        age = int(message.text.strip())
        await state.update_data(age=age)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="Низкая (сидячая работа)", callback_data="act_1.2")
        builder.button(text="Средняя (активность 1-3 р/нед)", callback_data="act_1.375")
        builder.button(text="Высокая (спорт 4-5 р/нед)", callback_data="act_1.55")
        builder.adjust(1)
        
        await message.answer("Выберите уровень физической активности:", reply_markup=builder.as_markup())
        await state.set_state(BotStates.waiting_for_activity)
    except ValueError:
        await message.answer("Введите корректное число лет (например: 25):")

@dp.callback_query(BotStates.waiting_for_activity, F.data.startswith("act_"))
async def process_activity(callback: types.CallbackQuery, state: FSMContext):
    factor = float(callback.data.split("_")[1])
    await state.update_data(activity_factor=factor)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="В день ☀️", callback_data="period_day")
    builder.button(text="В неделю 📅", callback_data="period_week")
    builder.button(text="В месяц 🗓️", callback_data="period_month")
    builder.adjust(3)
    
    await callback.message.answer("За какой период вам удобнее ограничить бюджет на еду?", reply_markup=builder.as_markup())
    await state.set_state(BotStates.waiting_for_budget_period)
    await callback.answer()

@dp.callback_query(BotStates.waiting_for_budget_period, F.data.startswith("period_"))
async def process_budget_period(callback: types.CallbackQuery, state: FSMContext):
    period = callback.data.split("_")[1]
    await state.update_data(budget_period=period)
    
    period_text = "в день" if period == "day" else "в неделю" if period == "week" else "в месяц"
    await callback.message.answer(f"Какую сумму (в рублях) вы готовы тратить на еду {period_text}?")
    await state.set_state(BotStates.waiting_for_budget_amount)
    await callback.answer()

@dp.message(BotStates.waiting_for_budget_amount)
async def process_budget_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        await state.update_data(budget_amount=amount)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="Пятёрочка 🛒", callback_data="store_пятерочка")
        builder.button(text="Магнит 🛍️", callback_data="store_магнит")
        builder.button(text="Самокат ⚡", callback_data="store_самокат")
        builder.button(text="Лента 🏬", callback_data="store_лента")
        builder.adjust(2)
        
        await message.answer("Выберите магазин для оценки цен:", reply_markup=builder.as_markup())
        await state.set_state(BotStates.waiting_for_store)
    except ValueError:
        await message.answer("Пожалуйста, введите сумму числом (например: 15000):")

@dp.callback_query(BotStates.waiting_for_store, F.data.startswith("store_"))
async def process_store(callback: types.CallbackQuery, state: FSMContext):
    store = callback.data.split("_")[1]
    data = await state.get_data()
    user_id = callback.from_user.id
    
    # Расчет КБЖУ
    calories, proteins, fats, carbs = calculate_kbzhu(
        gender=data["gender"],
        weight=data["weight"],
        height=data["height"],
        age=data["age"],
        activity_factor=data["activity_factor"]
    )
    
    # Приведение бюджета к ежедневному лимиту
    raw_amount = data["budget_amount"]
    period = data["budget_period"]
    if period == "month":
        daily_budget = raw_amount / 30.0
    elif period == "week":
        daily_budget = raw_amount / 7.0
    else:
        daily_budget = raw_amount
        
    USER_DATA[user_id] = {
        "diet": data["diet"],
        "gender": data["gender"],
        "weight": data["weight"],
        "height": data["height"],
        "age": data["age"],
        "activity_factor": data["activity_factor"],
        "daily_calories": calories,
        "proteins": proteins,
        "fats": fats,
        "carbs": carbs,
        "budget_limit": daily_budget, # дневной бюджет
        "store": store,
        "inventory": []
    }
    
    summary = (
        "📊 *Профиль питания успешно настроен!*\n\n"
        f"🏃‍♂️ *Суточная норма КБЖУ:*\n"
        f"▪️ Калории: *{calories} ккал*\n"
        f"▪️ Белки: *{proteins} г*\n"
        f"▪️ Жиры: *{fats} г*\n"
        f"▪️ Углеводы: *{carbs} г*\n\n"
        f"💰 *Ваш лимит бюджета:* {int(daily_budget)} руб/день\n"
        f"🏬 *Выбранная сеть:* {store.capitalize()}\n\n"
        "Используйте кнопки меню для добавления продуктов и подбора рецептов."
    )
    
    await callback.message.answer(summary, reply_markup=main_keyboard, parse_mode="Markdown")
    await state.clear()
    await callback.answer()

# --- ПОШАГОВЫЙ СБОР И КАТЕГОРИЗАЦИЯ ПРОДУКТОВ ---

async def ask_product_info(message: types.Message, state: FSMContext):
    data = await state.get_data()
    pending = data.get("pending_products", [])
    idx = data.get("current_index", 0)
    
    if idx >= len(pending):
        temp_products = data.get("temp_products", [])
        user_id = message.from_user.id
        if user_id not in USER_DATA:
            USER_DATA[user_id] = {"diet": "нет", "inventory": [], "budget_limit": 500, "store": "пятерочка"}
            
        for item in temp_products:
            USER_DATA[user_id]["inventory"].append(item)
            
        await message.answer("✅ Продукты добавлены в ваше наличие!", reply_markup=main_keyboard)
        await state.clear()
        return
        
    current_prod = pending[idx].strip()
    category = get_category(current_prod)
    unit = guess_unit(current_prod)
    
    await state.update_data(current_product_name=current_prod, current_category=category)
    await message.answer(
        f"Продукт: *{current_prod}* (Категория: {category})\nУкажите количество (например: 300 {unit} или 2 шт):",
        parse_mode="Markdown"
    )
    await state.set_state(BotStates.waiting_for_qty)

@dp.message(BotStates.waiting_for_qty)
async def process_qty(message: types.Message, state: FSMContext):
    qty = message.text.strip()
    data = await state.get_data()
    prod_name = data["current_product_name"]
    category = data["current_category"]
    
    await state.update_data(current_qty=qty)
    
    if needs_expiration(prod_name):
        await message.answer(
            f"У продукта *{prod_name}* короткий срок хранения. Укажите дату окончания (например, 25.12):",
            parse_mode="Markdown"
        )
        await state.set_state(BotStates.waiting_for_exp)
    else:
        temp_products = data.get("temp_products", [])
        temp_products.append({
            "name": prod_name,
            "category": category,
            "quantity": qty,
            "expiration": "длительный"
        })
        idx = data.get("current_index", 0) + 1
        await state.update_data(temp_products=temp_products, current_index=idx)
        await ask_product_info(message, state)

@dp.message(BotStates.waiting_for_exp)
async def process_exp(message: types.Message, state: FSMContext):
    exp = message.text.strip()
    data = await state.get_data()
    prod_name = data["current_product_name"]
    category = data["current_category"]
    qty = data["current_qty"]
    
    temp_products = data.get("temp_products", [])
    temp_products.append({
        "name": prod_name,
        "category": category,
        "quantity": qty,
        "expiration": exp
    })
    idx = data.get("current_index", 0) + 1
    await state.update_data(temp_products=temp_products, current_index=idx)
    await ask_product_info(message, state)

@dp.message(F.text == "📝 Закуп чек лист")
async def bulk_add_prompt(message: types.Message, state: FSMContext):
    await message.answer("Отправьте список продуктов через запятую (например: курица, молоко, помидоры, гречка):")
    await state.set_state(BotStates.waiting_for_bulk)

@dp.message(F.text == "➕ Добавить продукт")
async def single_add_prompt(message: types.Message, state: FSMContext):
    await message.answer("Введите название продукта для добавления:")
    await state.set_state(BotStates.waiting_for_bulk)

@dp.message(BotStates.waiting_for_bulk)
async def process_bulk_list(message: types.Message, state: FSMContext):
    products_input = message.text.split(",")
    products_list = [p.strip() for p in products_input if p.strip()]
    
    if not products_list:
        await message.answer("Список пуст. Попробуйте снова.")
        return
        
    await state.update_data(pending_products=products_list, current_index=0, temp_products=[])
    await ask_product_info(message, state)

# --- НАЛИЧИЕ ПРОДУКТОВ ---

@dp.message(F.text == "📦 Наличие продуктов")
async def show_inventory(message: types.Message):
    user_id = message.from_user.id
    user_info = USER_DATA.get(user_id, {"diet": "нет", "inventory": []})
    inv = user_info["inventory"]
    
    if not inv:
        await message.answer("Ваш список продуктов пуст. Добавьте продукты кнопками в меню.")
        return
        
    grouped = {}
    for item in inv:
        cat = item["category"]
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(item)
        
    text = "📦 *Ваши продукты по категориям:*\n\n"
    for cat, items in grouped.items():
        text += f"*{cat}*:\n"
        for item in items:
            exp_info = f" (до {item['expiration']})" if item['expiration'] != "длительный" else ""
            text += f" ▫️ {item['name'].capitalize()} — {item['quantity']}{exp_info}\n"
        text += "\n"
        
    await message.answer(text, parse_mode="Markdown")

# --- ПОДБОР РЕЦЕПТОВ С СРАВНЕНИЕМ СТОИМОСТИ ---

@dp.message(F.text == "🍳 Предложить рецепт")
async def show_recipe_types(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="Завтрак 🍳", callback_data="type_завтрак")
    builder.button(text="Обед 🍲", callback_data="type_обед")
    builder.button(text="Полдник 🍎", callback_data="type_полдник")
    builder.button(text="Ужин 🐟", callback_data="type_ужин")
    builder.button(text="Праздничное 🎉", callback_data="type_праздничное")
    builder.button(text="Постное 🥦", callback_data="type_постное")
    builder.adjust(2)
    
    await message.answer("Выберите категорию блюда:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("type_"))
async def list_recipes(callback: types.CallbackQuery):
    rtype = callback.data.split("_")[1]
    user_id = callback.from_user.id
    user_info = USER_DATA.get(user_id, {"diet": "нет", "inventory": [], "budget_limit": 500, "store": "пятерочка"})
    user_diet = user_info["diet"]
    user_store = user_info["store"]
    user_budget = user_info["budget_limit"]
    
    user_inv_names = [i["name"].lower().strip() for i in user_info["inventory"]]
    
    matched = []
    for r in RECIPES:
        if r["type"] != rtype:
            continue
            
        # Фильтрация по диете
        if user_diet == "веган" and "веган" not in r["diets"]:
            continue
        if user_diet == "непереносимость лактозы" and "без лактозы" not in r["diets"]:
            continue
        if user_diet == "диабет" and "диабет" not in r["diets"]:
            continue
            
        cost = calculate_recipe_cost(r["id"], user_store)
        
        # Допускаем блюдо, если его стоимость укладывается в 1/3 дневного бюджета (разовый прием пищи)
        meal_budget_limit = user_budget / 3.0
        budget_status = "✅ В бюджете" if cost <= meal_budget_limit else "⚠️ Выше лимита"
        
        matches = sum(1 for ing in r["ingredients"] if ing in user_inv_names)
        matched.append((r, matches, cost, budget_status))
        
    if not matched:
        await callback.message.answer("Рецептов с учетом ограничений диеты не найдено.")
        await callback.answer()
        return
        
    matched.sort(key=lambda x: x[1], reverse=True)
    
    builder = InlineKeyboardBuilder()
    for r, matches, cost, budget_status in matched:
        builder.button(
            text=f"{r['name']} ({cost}₽ | {matches}/{len(r['ingredients'])} совп.)",
            callback_data=f"recipe_{r['id']}"
        )
    builder.adjust(1)
    
    await callback.message.edit_text(
        f"Рецепты для категории *{rtype.capitalize()}* (Расчет цен по сети {user_store.capitalize()}):",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("recipe_"))
async def view_recipe(callback: types.CallbackQuery):
    recipe_id = callback.data.split("_")[1]
    user_id = callback.from_user.id
    user_info = USER_DATA.get(user_id, {"diet": "нет", "inventory": [], "budget_limit": 500, "store": "пятерочка"})
    user_inv_names = [i["name"].lower().strip() for i in user_info["inventory"]]
    user_store = user_info["store"]
    user_budget = user_info["budget_limit"]
    
    recipe = next((r for r in RECIPES if r["id"] == recipe_id), None)
    if not recipe:
        await callback.answer("Рецепт не найден.")
        return
        
    cost = calculate_recipe_cost(recipe["id"], user_store)
    meal_limit = int(user_budget / 3)
    
    ing_text = ""
    has_all = True
    for ing in recipe["ingredients"]:
        if ing in user_inv_names:
            ing_text += f"✅ {ing.capitalize()}\n"
        else:
            has_all = False
            sub = recipe["substitutions"].get(ing, "нет замены")
            ing_text += f"❌ {ing.capitalize()} (Замена: {sub})\n"
            
    text = (
        f"🍳 *{recipe['name']}*\n\n"
        f"📊 *КБЖУ:*\n"
        f"Калории: {recipe['calories']} ккал\n"
        f"Углеводы: {recipe['carbs']} г\n\n"
        f"💰 *Оценка бюджета:*\n"
        f"▪️ Стоимость в *{user_store.capitalize()}*: {cost} руб.\n"
        f"▪️ Лимит на один прием пищи: {meal_limit} руб.\n"
        f"▪️ Статус: {'✅ Подходит' if cost <= meal_limit else '⚠️ Превышает лимит на прием пищи'}\n\n"
        f"🛒 *Ингредиенты:*\n{recipe['composition']}\n\n"
        f"📌 *Наличие на вашей кухне:*\n{ing_text}\n"
        f"📖 *Инструкция:*\n{recipe['instructions']}"
    )
    
    builder = InlineKeyboardBuilder()
    if has_all:
        builder.button(text="🍳 Приготовить блюдо", callback_data=f"cook_{recipe['id']}")
    else:
        builder.button(text="🍳 Приготовить с заменой", callback_data=f"cook_{recipe['id']}")
    builder.button(text="⬅️ Назад", callback_data=f"type_{recipe['type']}")
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("cook_"))
async def cook_recipe(callback: types.CallbackQuery):
    recipe_id = callback.data.split("_")[1]
    user_id = callback.from_user.id
    user_info = USER_DATA.get(user_id, {"diet": "нет", "inventory": []})
    
    recipe = next((r for r in RECIPES if r["id"] == recipe_id), None)
    if not recipe:
        await callback.answer("Рецепт не найден.")
        return
        
    inv = user_info["inventory"]
    removed = []
    new_inv = []
    
    for item in inv:
        if item["name"].lower().strip() in recipe["ingredients"]:
            removed.append(item["name"])
        else:
            new_inv.append(item)
            
    USER_DATA[user_id]["inventory"] = new_inv
    removed_str = ", ".join(removed) if removed else "ничего"
    
    await callback.message.answer(
        f"🎉 Приятного аппетита! Наличие продуктов обновлено.\n"
        f"Из списка наличия списано: {removed_str}."
    )
    await callback.message.delete()
    await callback.answer()

# --- РАССЫЛКА В НАЧАЛЕ МЕСЯЦА ---

async def send_monthly_reminder():
    for user_id in USER_DATA.keys():
        try:
            USER_DATA[user_id]["inventory"] = [] # Обнуляем запасы
            await bot.send_message(
                user_id,
                "📅 Начался новый месяц! Пожалуйста, обновите список купленных продуктов через кнопку 'Закуп чек лист'."
            )
        except Exception as e:
            logging.error(f"Ошибка напоминания для {user_id}: {e}")

@dp.message(Command("test_month"))
async def trigger_test_month(message: types.Message):
    user_id = message.from_user.id
    if user_id in USER_DATA:
        USER_DATA[user_id]["inventory"] = []
    await message.answer(
        "📅 (Тест нового месяца) Ваши запасы сброшены. Пожалуйста, введите список новых продуктов через запятую."
    )

async def main():
    scheduler.add_job(send_monthly_reminder, "cron", day=1, hour=9, minute=0)
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
