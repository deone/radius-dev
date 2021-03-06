#! /usr/bin/env python

import rules

import unittest
from datetime import timedelta
from decimal import Decimal

import django
django.setup()

from django.contrib.auth.models import User
from django.utils import timezone

from billing.settings import PACKAGE_TYPES_HOURS_MAP
from accounts.helpers import md5_password
from accounts.models import Radcheck, Subscriber, GroupAccount, AccessPoint
from packages.models import (Package, PackageSubscription, GroupPackageSubscription, InstantVoucher)

class AccountingTestCase(unittest.TestCase):

    def setUp(self):
        self.start = (
            ('User-Name', '"c@c.com"'),
            ('Acct-Status-Type', 'Start'),
            )

        self.stop = (
            ('User-Name', '"c@c.com"'),
            ('Acct-Status-Type', 'Stop'),
            ('Acct-Input-Octets', '255909888'),
            ('Acct-Output-Octets', '19741696'),
            )

        self.excess_octets_stop = (
            ('User-Name', '"c@c.com"'),
            ('Acct-Status-Type', 'Stop'),
            ('Acct-Input-Octets', '600000000'),
            ('Acct-Output-Octets', '500000000'),
            )

        self.username = 'c@c.com'
        self.password = '12345'
        self.group = GroupAccount.objects.create(name='CUG', max_no_of_users=10, data_balance=1)
        self.user = User.objects.create_user(self.username, self.username, self.password)
        self.subscriber = Subscriber.objects.create(user=self.user, country='NGA', phone_number='+2348029299274')
        self.radcheck = Radcheck.objects.create(user=self.user, username=self.username,
            attribute='MD5-Password', op=':=', value=md5_password(self.password), is_logged_in=True, data_balance=1)

    def test_accounting_start(self):
        result = rules.accounting(self.start)
        self.assertEqual(result, 2)

    """ def test_accounting_stop_individual(self):
        # check whether OK is returned
        result = rules.accounting(self.stop)

        radcheck = Radcheck.objects.get(username=self.radcheck.username)
        self.assertEqual(radcheck.data_balance, Decimal('0.72'))
        self.assertEqual(result, 2)

    def test_accounting_stop_individual_negative(self):
        result = rules.accounting(self.excess_octets_stop)

        radcheck = Radcheck.objects.get(username=self.radcheck.username)
        self.assertEqual(radcheck.data_balance, Decimal('0.00'))
        self.assertEqual(result, 2)

    def test_accounting_stop_group_member(self):
        self.subscriber.group = self.group
        self.subscriber.save()

        result = rules.accounting(self.stop)
        group = GroupAccount.objects.get(name=self.group.name)
        self.assertEqual(group.data_balance, Decimal('0.72'))
        self.assertEqual(result, 2)

    def test_accounting_stop_group_member_negative(self):
        self.subscriber.group = self.group
        self.subscriber.save()

        result = rules.accounting(self.excess_octets_stop)

        group = GroupAccount.objects.get(name=self.group.name)
        self.assertEqual(group.data_balance, Decimal('0.00'))
        self.assertEqual(result, 2) """

    def tearDown(self):
        self.user.delete()
        self.group.delete()

class AuthorizeTestCase(unittest.TestCase):

    def setUp(self):
        self.p = (
            ('Acct-Session-Id', '"624874448299458941"'),
            ('Called-Station-Id', '"00-18-0A-F2-DE-15:Radius test"'),
            ('Calling-Station-Id', '"48-D2-24-43-A6-C1"'),
            ('Framed-IP-Address', '172.31.3.142'),
            ('NAS-Identifier', '"Cisco Meraki cloud RADIUS client"'),
            ('NAS-IP-Address', '108.161.147.120'),
            ('NAS-Port', '0'),
            ('NAS-Port-Id', '"Wireless-802.11"'),
            ('NAS-Port-Type', 'Wireless-802.11'),
            ('Service-Type', 'Login-User'),
            ('User-Name', '"c@c.com"'),
            ('User-Password', '"12345"'),
            ('Attr-26.29671.1', '0x446a756e676c65204851203032')
            )

        self.ap = AccessPoint.objects.create(name='My AP', mac_address='00:18:0A:F2:DE:15')

class NotFoundTestCase(AuthorizeTestCase):

    def setUp(self, *args, **kwargs):
        super(NotFoundTestCase, self).setUp(*args, **kwargs)

    def test_user_voucher_None(self):
        result = rules.authorize(self.p)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1][0], ('Reply-Message', 'User account or Voucher does not exist.'))

    def test_ap_not_found(self):
        p = (
            ('Called-Station-Id', '"00-18-0A-F2-DE-18:Radius test"'),
            )

        result = rules.authorize(p)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1][0], ('Reply-Message', 'AP Not Found. Please call customer care.'))

    def tearDown(self):
        self.ap.delete()

class AuthorizeVoucherTestCase(AuthorizeTestCase):

    def setUp(self, *args, **kwargs):
        super(AuthorizeVoucherTestCase, self).setUp(*args, **kwargs)
        self.voucher = Radcheck.objects.create(user=None, username='c@c.com',
            attribute='MD5-Password', op=':=', value=md5_password('12345'), data_balance=1)
        self.package = Package.objects.create(package_type='Daily', volume='3', speed='1.5', price=4)
        self.iv = InstantVoucher.objects.create(radcheck=self.voucher, package=self.package)
        self.ap.status = 'PUB'
        self.ap.save()

    def test_voucher_password_incorrect(self):
        self.voucher.value = md5_password('00000')
        self.voucher.save()

        result = rules.authorize(self.p)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1][0], ('Reply-Message', 'Voucher Password Incorrect'))

    def test_user_unauthorized(self):
        self.ap.status = 'PRV'
        self.ap.save()

        result = rules.authorize(self.p)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1][0], ('Reply-Message', 'User Unauthorized.'))

    def test_authorize_response(self):
        result = rules.authorize(self.p)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], 2)
        self.assertEqual(result[1][0][0], 'Session-Timeout')
        self.assertEqual(result[1][1][0], 'Acct-Interim-Interval')
        self.assertEqual(result[1][2][0], 'Maximum-Data-Rate-Upstream')
        self.assertEqual(result[1][3][0], 'Maximum-Data-Rate-Downstream')

    def tearDown(self):
        self.ap.delete()
        self.voucher.delete()
        self.package.delete()
        self.iv.delete()


class AuthorizeUserTestCase(AuthorizeTestCase):

    def setUp(self, *args, **kwargs):
        super(AuthorizeUserTestCase, self).setUp(*args, **kwargs)
        self.ap.status = 'PUB'
        self.ap.save()

        self.username = 'c@c.com'
        self.password = '12345'
        self.user = User.objects.create_user(self.username, self.username, self.password)
        Subscriber.objects.create(user=self.user, country='NGA', phone_number='+2348029299274')

    def test_user_has_no_subscription(self):
        result = rules.authorize(self.p)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1][0], ('Reply-Message', "You have no subscription. Click 'Manage Account' below to recharge your account and purchase a package."))

    def test_user_password_incorrect(self):
        self.user.set_password('00000')
        self.user.save()

        result = rules.authorize(self.p)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1][0], ('Reply-Message', 'User Password Incorrect'))

    def test_user_inactive(self):
        self.user.is_active = False
        self.user.save()

        result = rules.authorize(self.p)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1][0], ('Reply-Message', 'User Inactive'))

    def tearDown(self):
        self.ap.delete()
        self.user.delete()


class FunctionsTestCase(unittest.TestCase):

    def setUp(self):
        self.p = (
            ('Acct-Session-Id', '"624874448299458941"'),
            ('Called-Station-Id', '"00-18-0A-F2-DE-11:Radius test"'),
            ('Calling-Station-Id', '"48-D2-24-43-A6-C1"'),
            ('Framed-IP-Address', '172.31.3.142'),
            ('NAS-Identifier', '"Cisco Meraki cloud RADIUS client"'),
            ('NAS-IP-Address', '108.161.147.120'),
            ('NAS-Port', '0'),
            ('NAS-Port-Id', '"Wireless-802.11"'),
            ('NAS-Port-Type', 'Wireless-802.11'),
            ('Service-Type', 'Login-User'),
            ('User-Name', '"a@a.com"'),
            ('User-Password', '"12345"'),
            # ('User-Name', '"dayo@thecodeschool.net"'),
            # ('User-Password', '"12345"'),
            # ('User-Name', '"ulyh@spectrawireless.com"'),
            # ('User-Password', '"EYQL9B"'),
            ('Attr-26.29671.1', '0x446a756e676c65204851203032')
            )
        
        self.now = timezone.now()
        self.params = dict(self.p)
        self.username = rules.trim_value(self.params['User-Name'])
        self.password = rules.trim_value(self.params['User-Password'])

        self.user = User.objects.create_user(self.username, self.username, self.password)
        self.group = GroupAccount.objects.create(name='CUG', max_no_of_users=10)
        self.subscriber = Subscriber.objects.create(user=self.user, country='NGA', phone_number='+2348029299274', group=self.group)
        self.voucher = Radcheck.objects.create(user=self.user, username=self.username,
            attribute='MD5-Password', op=':=', value=md5_password(self.password), data_balance=1)
        self.package = Package.objects.create(package_type='Daily',
            volume='3', speed='1.5', price=4)
        self.ivoucher = InstantVoucher.objects.create(radcheck=self.voucher, package=self.package)
        self.ap = AccessPoint.objects.create(name='My AP', mac_address='00:18:0A:F2:DE:11')

    def test_instantiate(self):
        self.assertTrue(rules.instantiate(self.p))

    def test_create_mac(self):
        mac = rules.create_mac(self.params['Called-Station-Id'])
        self.assertEqual(mac, '00:18:0A:F2:DE:11')

    def test_get_or_create_subscription(self):
        subscription = rules.get_or_create_subscription(self.voucher)
        self.assertTrue(isinstance(subscription, PackageSubscription))
        subscription.delete()

    def test_set_logged_in(self):
        self.assertTrue(rules.set_logged_in(self.user))

    def test_get_user_subscription(self):
        self.user.subscriber.group = None
        rules.create_subscription(self.voucher, self.package)
        subscription = rules.get_user_subscription(self.user)
        self.assertTrue(isinstance(subscription, PackageSubscription))
        subscription.delete()

    def test_get_user_subscription_None(self):
        subscription = rules.get_user_subscription(self.user)
        self.assertEqual(subscription, None)

    def test_get_user_subscription_group_valid(self):
        gps = GroupPackageSubscription.objects.create(group=self.group, package=self.package, start=self.now,
            stop=self.now + timedelta(hours=PACKAGE_TYPES_HOURS_MAP[self.package.package_type]))

        subscription = rules.get_user_subscription(self.user)
        self.assertTrue(isinstance(subscription, GroupPackageSubscription))
        gps.delete()

    def test_get_user_subscription_group_IndexError(self):
        subscription = rules.get_user_subscription(self.user)
        self.assertEqual(subscription, None)

    def test_get_user(self):
        user = rules.get_user(self.username)
        self.assertTrue(isinstance(user, User))

    def test_get_user_None(self):
        self.assertEqual(rules.get_user('hhhh'), None)

    def test_get_voucher(self):
        username = 'aaaa'
        voucher = Radcheck.objects.create(user=None, username=username,
            attribute='MD5-Password', op=':=', value=md5_password('12345'))
        self.assertTrue(isinstance(rules.get_voucher(username), Radcheck))
        voucher.delete()

    def test_get_voucher_None(self):
        self.assertEqual(rules.get_voucher('bbbb'), None)

    def test_get_ap(self):
        ap = rules.get_ap('00:18:0A:F2:DE:11')
        self.assertTrue(isinstance(ap, AccessPoint))

    def test_get_ap_None(self):
        self.assertEqual(rules.get_ap('00:18:0A:F2:DE:12'), None)

    def test_check_voucher_password_valid(self):
        self.assertTrue(rules.check_voucher_password(self.voucher, '12345'))

    def test_check_voucher_password_invalid(self):
        invalid = rules.check_voucher_password(self.voucher, '00000')
        self.assertEqual(invalid, 'VPI')
        self.assertEqual(rules.REPLY_CODES_MESSAGES[invalid], 'Voucher Password Incorrect')

    def test_check_user_password_valid(self):
        self.assertTrue(rules.check_user_password(self.user, '12345'))

    def test_check_user_password_invalid(self):
        invalid = rules.check_user_password(self.user, '00000')
        self.assertEqual(invalid, 'UPI')
        self.assertEqual(rules.REPLY_CODES_MESSAGES[invalid], 'User Password Incorrect')

    def test_check_user_account_status_valid(self):
        self.assertTrue(rules.check_user_account_status(self.user))

    def test_check_user_account_status_invalid(self):
        self.user.is_active = False
        self.user.save()
        invalid = rules.check_user_account_status(self.user)
        self.assertEqual(invalid, 'UIN')
        self.assertEqual(rules.REPLY_CODES_MESSAGES[invalid], 'User Inactive')

    def test_check_user_eligibility_on_ap_valid(self):
        self.ap.status = 'PUB'
        self.ap.save()
        self.assertTrue(rules.check_user_eligibility_on_ap(self.user, self.ap))

    def test_check_user_eligibility_on_ap_invalid(self):
        self.assertFalse(rules.check_user_eligibility_on_ap(self.user, self.ap))

    def test_check_subscription_validity_valid(self):
        subscription = rules.get_or_create_subscription(self.voucher)
        response = rules.check_subscription_validity(subscription, self.user)
        self.assertEqual(len(response), 3)
        self.assertEqual(response[0], 2)
        subscription.delete()

    def test_check_subscription_validity_invalid(self):
        subscription = rules.get_or_create_subscription(self.voucher)
        subscription.stop = self.now - timedelta(hours=PACKAGE_TYPES_HOURS_MAP[subscription.package.package_type])
        subscription.save()
        response = rules.check_subscription_validity(subscription, self.user)
        self.assertEqual(len(response), 3)
        self.assertEqual(response[0], 0)
        subscription.delete()

    def tearDown(self):
        self.user.delete() # This also deletes self.subscriber
        self.group.delete()
        self.voucher.delete()
        self.ivoucher.delete()
        self.package.delete()
        self.ap.delete()

# suite = unittest.TestSuite([AuthorizeTestCase('test_success'), AuthorizeTestCase('test_fail')])
# suite.run(unittest.TestResult())

if __name__ == "__main__":
    unittest.main()
