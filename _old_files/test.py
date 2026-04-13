import asyncio
#
# async def say_hello(name, delay):
#     await asyncio.sleep(delay)
#     print(f"Привет, {name}!")
#
# async def main():
#     # Запускаем три задачи параллельно
#     task1 = asyncio.create_task(say_hello("Аня", 2))
#     task2 = asyncio.create_task(say_hello("Боря", 1))
#     task3 = asyncio.create_task(say_hello("Витя", 3))
#
#     print("Запустили все задачи")
#     await task1   # ждём завершения
#     await task2
#     await task3
#     print("Все закончили")
#
# asyncio.run(main())


async def background_job():
    print("Фоновая работа")
    await asyncio.sleep(5)
    print("Фоновая работа 2")

async def background_job2():
    print("Фоновая работа 3")
    await asyncio.sleep(5)
    print("Фоновая работа 4")

async def main():
    while True:
        asyncio.create_task(background_job())
        asyncio.create_task(background_job2())
        await asyncio.sleep(1)


asyncio.run(main())