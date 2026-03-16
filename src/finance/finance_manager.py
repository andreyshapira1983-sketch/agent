class FinanceManager:
    def __init__(self):
        self.income = 0
        self.expenses = 0
        self.balance = 0

    def add_income(self, amount):
        self.income += amount
        self.update_balance()

    def add_expense(self, amount):
        self.expenses += amount
        self.update_balance()

    def update_balance(self):
        self.balance = self.income - self.expenses

    def get_balance(self):
        return self.balance

    def get_financial_report(self):
        report = {
            'income': self.income,
            'expenses': self.expenses,
            'balance': self.balance
        }
        return report
