def format_money(value):
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def format_calculation_result(result):
    text = (
        "📊 Предварительный расчет готов\n\n"
        f"Стоимость по ДДУ: {format_money(result.contract_price)}\n"
        f"Дней просрочки: {result.delay_days}\n"
        f"Предварительная сумма неустойки: {format_money(result.penalty_amount)}\n\n"
        "Важно: расчет предварительный. Итоговая сумма зависит от документов, условий ДДУ, "
        "дополнительных соглашений и позиции суда."
    )
    return text + (f"\n\n⚠️ {result.warning}" if result.warning else "")
