class PersonalIdentity:
    def __init__(self, name: str, gender: str):
        self.name = name  # Имя агента
        self.gender = gender  # Пол агента
        self.bio = self.generate_bio()

    def generate_bio(self):
        return f"Привет, меня зовут {self.name}, и я {self.gender} агент, созданный для помощи в различных задачах." 

    def introduce(self):
        return self.bio

# Пример создания идентичности
agent_identity = PersonalIdentity(name='Агент', gender='неопределённый')
print(agent_identity.introduce())  # С введённой строкой при обращении к агенту.
