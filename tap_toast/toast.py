
#
# Module dependencies.
#

from requests.auth import HTTPBasicAuth
from datetime import date, datetime, timedelta, timezone
from singer import utils
import backoff
import requests
import logging
import pytz
import sys
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

logger = logging.getLogger()
utc = pytz.UTC

class ProgressTracker:
    def __init__(self, total_hours):
        self.total_hours = total_hours
        self.completed_hours = 0
        self.total_orders_fetched = 0
        self.lock = threading.Lock()

    def increment_hour(self, orders_in_hour):
        with self.lock:
            self.completed_hours += 1
            self.total_orders_fetched += orders_in_hour
            pct = (self.completed_hours / self.total_hours * 100) if self.total_hours > 0 else 0
            logger.info(
                'Progress: [{}/{} hours] ({:.1f}%) - {} orders fetched so far'.format(
                    self.completed_hours,
                    self.total_hours,
                    pct,
                    self.total_orders_fetched
                )
            )



def get_start_end_hour(start_date, end_date):
    delta = timedelta(hours=1)
    format_string = '%Y-%m-%dT%H:%M:%S.000-0000' # hard coding this timezone because it's too complicated
    while start_date < end_date:
        yield (start_date.strftime(format_string), (start_date + delta).strftime(format_string))
        start_date += delta



def daterange(start_date, end_date):
    for n in range(int ((end_date - start_date).days)):
        yield start_date + timedelta(n)



class Toast(object):

    def __init__(self, client_id=None, client_secret=None, location_guid=None, management_group_guid=None, start_date=None):
        """ Simple Python wrapper for the Toast API. """
        self.host = 'https://ws-api.toasttab.com/'
        self.client_id = client_id
        self.client_secret = client_secret
        self.location_guid = location_guid
        self.management_group_guid = management_group_guid
        self.start_date = utils.strptime_with_tz(start_date)
        self.authorization_token = None
        self.fmt_date_time = '%Y-%m-%dT%H:%M:%S.%Z'
        self.fmt_date = '%Y%m%d'
        self.default_page_size = 50
        self.get_authorization_token()
        # print(self.authorization_token)


    def _url(self, path):
        return self.host + path


    @backoff.on_exception(backoff.expo,
                        requests.exceptions.RequestException)
    def _post(self, url, **kwargs):
        if self.authorization_token is None:
            self.get_authorization_token()

        header = { 'Authorization': 'Bearer ' + self.authorization_token, 'Toast-Restaurant-External-ID': self.location_guid, 'Content-Type': 'application/json' }
        response = requests.post(url, headers=header)
        response.raise_for_status()
        logger.info('POST request successful at {url}'.format(url=url))
        return response.json()


    @backoff.on_exception(backoff.expo,
                        requests.exceptions.RequestException)
    def _get(self, url, **kwargs):
        if self.authorization_token is None:
            self.get_authorization_token()

        header = { 'Authorization': 'Bearer ' + self.authorization_token, 'Toast-Restaurant-External-ID': self.location_guid, 'Content-Type': 'application/json' }
        response = requests.get(url, headers=header, params=kwargs)
        response.raise_for_status()
        logger.info('GET request successful at {url}'.format(url=url))
        try:
            res = response.json()
            if isinstance(res, dict):
                res = [res]
        except ValueError:
            res = []
        return res


    def is_authorized(self):
        return self.authorization_token is not None


    def get_authorization_token(self):
        payload = { 'clientId': self.client_id, 'clientSecret': self.client_secret, 'userAccessType': 'TOAST_MACHINE_CLIENT' }
        response = requests.post(self._url('authentication/v1/authentication/login'), json=payload)
        response.raise_for_status()
        res = response.json()
        logger.info('Authorization successful.')
        self.authorization_token = res['token']['accessToken']


    # column_name, bookmark
    def cash_management_entries(self, column_name=None, bookmark=None):
        business_date = utils.strptime_with_tz(bookmark).strftime(self.fmt_date)
        for single_date in daterange(utils.strptime_with_tz(business_date), datetime.now(pytz.utc)):
            logger.info('Hitting cash management entries endpoint at datetime {date}'.format(date=single_date))
            res = self._get(self._url('cashmgmt/v1/entries'), businessDate=single_date.strftime(self.fmt_date))
            logger.info('Returned {number} entries.'.format(number=len(res)))
            for item in res:
                yield item


    # column_name, bookmark
    def cash_management_deposits(self, column_name=None, bookmark=None):
        business_date = utils.strptime_with_tz(bookmark).strftime(self.fmt_date)
        for single_date in daterange(utils.strptime_with_tz(business_date), datetime.now(pytz.utc)):
            logger.info('Hitting cash management deposits endpoint at date {date}'.format(date=single_date))
            res = self._get(self._url('cashmgmt/v1/deposits'), businessDate=single_date.strftime(self.fmt_date))
            logger.info('Returned {number} deposits.'.format(number=len(res)))
            for item in res:
                yield item


    # full table sync
    def employees(self, column_name=None, bookmark=None):
        res = self._get(self._url('labor/v1/employees'))
        for item in res:
            yield item


    def orders(self, column_name=None, bookmark=None):
        business_date = utils.strptime_with_tz(bookmark).strftime(self.fmt_date_time)
        hours = list(get_start_end_hour(utils.strptime_with_tz(business_date), datetime.now(pytz.utc)))
        total_hours = len(hours)

        logger.info('Starting orders sync: {} hours to process'.format(total_hours))

        progress = ProgressTracker(total_hours)
        max_workers = 10

        def fetch_order_details(order_guid):
            guid = order_guid if isinstance(order_guid, str) else order_guid.get('guid')
            if not guid:
                return None
            try:
                order_data = self._get(self._url('orders/v2/orders/{order_guid}'.format(order_guid=guid)))
                if order_data:
                    return order_data[0]
            except Exception as e:
                logger.warning('Failed to fetch order {}: {}'.format(guid, e))
            return None

        for (start_hour, end_hour) in hours:
            logger.info('Fetching order GUIDs for hour: {}'.format(start_hour))
            order_guids = self._get(self._url('orders/v2/orders/'), startDate=start_hour, endDate=end_hour)
            logger.info('Found {} orders in hour {}'.format(len(order_guids), start_hour))

            current_hour_orders = []
            if order_guids:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(fetch_order_details, guid): guid for guid in order_guids}
                    for future in as_completed(futures):
                        order = future.result()
                        if order is not None:
                            current_hour_orders.append(order)

            progress.increment_hour(len(order_guids))

            for order in current_hour_orders:
                yield order


    def payments(self, column_name=None, bookmark=None):
        business_date = utils.strptime_with_tz(bookmark).strftime(self.fmt_date)
        dates = list(daterange(utils.strptime_with_tz(business_date), datetime.now(pytz.utc)))
        total_dates = len(dates)

        logger.info('Starting payments sync: {} dates to process'.format(total_dates))

        progress = ProgressTracker(total_dates)
        max_workers = 10

        def fetch_payment_details(payment_guid):
            guid = payment_guid if isinstance(payment_guid, str) else payment_guid.get('guid')
            if not guid:
                return None
            try:
                payment_data = self._get(self._url('orders/v2/payments/{payment_guid}'.format(payment_guid=guid)))
                if payment_data:
                    return payment_data[0]
            except Exception as e:
                logger.warning('Failed to fetch payment {}: {}'.format(guid, e))
            return None

        for single_date in dates:
            logger.info('Fetching payments for date: {}'.format(single_date))
            paid_res = self._get(self._url('orders/v2/payments'), paidBusinessDate=single_date.strftime(self.fmt_date))
            refund_res = self._get(self._url('orders/v2/payments'), refundBusinessDate=single_date.strftime(self.fmt_date))
            void_res = self._get(self._url('orders/v2/payments'), voidBusinessDate=single_date.strftime(self.fmt_date))
            res = paid_res + refund_res + void_res
            logger.info('Found {} payments for date {}'.format(len(res), single_date))

            current_date_payments = []
            if res:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(fetch_payment_details, guid): guid for guid in res}
                    for future in as_completed(futures):
                        payment = future.result()
                        if payment is not None:
                            current_date_payments.append(payment)

            progress.increment_hour(len(res))

            for payment in current_date_payments:
                yield payment


    def alternate_payment_types(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/alternatePaymentTypes'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def break_types(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/breakTypes'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def cash_drawers(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/cashDrawers'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def dining_options(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/diningOptions'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def discounts(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/discounts'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def menu_groups(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/menuGroups'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def menu_items(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/menuItems'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def menu_option_groups(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/menuOptionGroups'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def menus(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/menus'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def no_sale_reasons(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/noSaleReasons'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def payout_reasons(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/payoutReasons'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def premodifier_groups(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/preModifierGroups'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def premodifiers(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/preModifiers'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def price_groups(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/priceGroups'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def printers(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/printers'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def restaurant_services(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/restaurantServices'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def revenue_centers(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/revenueCenters'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def sales_categories(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/salesCategories'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def service_areas(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/serviceAreas'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def tables(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/tables'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def tax_rates(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/taxRates'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def tip_withholding(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/tipWithholding'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def void_reasons(self, column_name=None, bookmark=None):
        res = self._get(self._url('config/v2/voidReasons'), pageSize=self.default_page_size)
        for item in res:
            yield item


    def restaurants(self, column_name=None, bookmark=None):
        restaurant_ids = self._get(self._url('restaurants/v1/groups/{management_group_guid}/restaurants'.format(management_group_guid=self.management_group_guid)))
        for restaurant_id in restaurant_ids:
            restaurants = self._get(self._url('restaurants/v1/restaurants/{restaurant_guid}'.format(restaurant_guid=restaurant_id["guid"])))
            for restaurant in restaurants:
                yield restaurant


