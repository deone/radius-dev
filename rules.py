#! /usr/bin/env python

import os
import time
import subprocess
from decimal import Decimal
from datetime import timedelta

import radiusd

radiusd.radlog(radiusd.L_INFO, "*** Setting settings module ***")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing.settings")
radiusd.radlog(radiusd.L_INFO, "*** - Settings module set successfully ***")

radiusd.radlog(radiusd.L_INFO, "*** Importing and setting up Django elements ***")
import django
django.setup()

from django.contrib.auth.models import User
from django.utils import timezone

from billing.settings import PACKAGE_TYPES_HOURS_MAP
from accounts.models import AccessPoint, Radcheck, GroupAccount
from accounts.helpers import md5_password
from packages.models import PackageSubscription, InstantVoucher


REPLY_CODES_MESSAGES = {
    'VPI': 'Voucher Password Incorrect',
    'UPI': 'User Password Incorrect',
    'UIN': 'User Inactive',
}

def print_info(info):
    radiusd.radlog(radiusd.L_INFO, info)

def trim_value(val):
    return val[1:-1]

def create_subscription(voucher, package):
    now = timezone.now()
    ps = PackageSubscription.objects.create(
            radcheck=voucher, package=package, start=now,
            stop=now + timedelta(hours=PACKAGE_TYPES_HOURS_MAP[package.package_type]))

    return ps

##############################

def create_mac(param):
    called_station_id = trim_value(param).split(':')[0]
    return called_station_id.replace('-', ':')

# For simplicity, make Package Subscription reference Radcheck instead of Subscriber for now.
# - Eventually, move all extra user info into Radcheck and rename it to Subscriber. Password check will then happen in Subscriber (md5).
# Check for package subscription. If subscription exists, skip next step.
# Fetch subscriber - query User with username. If not found, query Radcheck with username. If found in Radcheck, create PackageSubscription.
# For instant users, check password by md5-hashing password and comparing it with password in Radcheck.
# Skip account status check for instant users - log this to avoid confusion.
# To check AP eligibilty for instant users, return False in the 'else' block of AccessPoint.allows() if user is not an instance of User.

def get_or_create_subscription(voucher):
    try:
        subscription = PackageSubscription.objects.get(radcheck__username=voucher.username)
    except PackageSubscription.DoesNotExist:
        ivoucher = InstantVoucher.objects.get(radcheck__username=voucher.username)
        subscription = create_subscription(voucher, ivoucher.package)

    return subscription

def get_user_subscription(user):
    if user.subscriber.group is not None:
        try:
	    subscription = user.subscriber.group.grouppackagesubscription_set.all()[0]
	except:
	    return None
    else:
	try:
            subscription = user.radcheck.packagesubscription_set.all()[0]
	except:
	    return None

    return subscription

def get_user(username):
    try:
        user = User.objects.get(username__exact=username)
    except User.DoesNotExist:
        return None
    else:
        return user

def get_voucher(username):
    try:
        voucher_list = Radcheck.objects.filter(user=None).filter(username__exact=username)
        voucher = voucher_list[0]
    except IndexError:
        return None
    else:
        return voucher

def get_ap(ap_mac):
    try:
        ap = AccessPoint.objects.get(mac_address__exact=ap_mac)
    except AccessPoint.DoesNotExist:
        return None
    else:
        return ap

def check_voucher_password(voucher_password, user_password):
    if md5_password(user_password) != voucher_password:
	return 'VPI'
    else:
        return True

def check_user_password(user, password):
    if not user.check_password(password):
        return 'UPI'
    else:
        return True 

def check_user_account_status(user):
    if user.is_active:
        return True
    else:
        return 'UIN'

def check_user_eligibility_on_ap(user, ap):
    if ap.allows(user):
        return True
    else:
        return False

def set_logged_in(user):
    try:
        subscriber = user.subscriber
    except:
        pass
    else:
        if subscriber.group is not None:
            user.radcheck.is_logged_in = True
            user.radcheck.save()

    return user

def check_subscription_validity(subscription, user):
    if subscription.is_valid():
        now = timezone.now()

        package_period = str((subscription.stop - now).total_seconds())
        package_period = package_period.split(".")[0]

        bandwidth_limit = str(float(subscription.package.speed) * 1000000)
        bandwidth_limit = bandwidth_limit.split('.')[0]

        set_logged_in(user)

        return (radiusd.RLM_MODULE_OK,
            (('Session-Timeout', package_period),('Acct-Interim-Interval', '300'),('Maximum-Data-Rate-Upstream', bandwidth_limit),('Maximum-Data-Rate-Downstream', bandwidth_limit)),
            (('Auth-Type', 'python'),))
    else:
        return (radiusd.RLM_MODULE_REJECT,
            (('Reply-Message', 'Subscription Invalid'),), (('Auth-Type', 'python'),))

def display_reply_message(error_code):
    return (radiusd.RLM_MODULE_REJECT,
            (('Reply-Message', REPLY_CODES_MESSAGES[error_code]),), (('Auth-Type', 'python'),))

def send_disconnect_request(acct_session_id):
    payload = "Acct-Session-Id = " + acct_session_id + ",Event-Timestamp = '" + str(int(round(time.time()))) + "'"
    echo = subprocess.Popen(['echo', payload], stdout=subprocess.PIPE)
    output = subprocess.check_output(['radclient', '-x', 'n110.meraki.com:3799', 'disconnect', 'testing123'], stdin=echo.stdout)
    return output



def instantiate(p):
    print_info('*** Instantiating Python module ***')
    return True

def authorize(p):
    print_info("*** Response Content: " + str(p) + " ***")
    params = dict(p)
    ap_mac = create_mac(params['Called-Station-Id'])

    # Fetch AP
    print_info('*** Fetching AP... ***')
    ap = get_ap(ap_mac)

    if not ap:
        print_info('*** - AP Not Found ***')
        return (radiusd.RLM_MODULE_REJECT,
            (('Reply-Message', 'AP Not Found. Please call customer care.'),), (('Auth-Type', 'python'),))
    else:
        print_info('*** - AP fetched successfully: ' + ap.mac_address + ' ***')

    username = trim_value(params['User-Name'])
    password = trim_value(params['User-Password'])

    # Fetch user/voucher
    user = None
    voucher = None

    print_info('*** Fetching User... ***')
    user = get_user(username)

    if not user:
        print_info('*** - User Not Found ***')
        print_info("*** Okay. Might be a voucher. Let's try fetching Voucher... ***")
        voucher = get_voucher(username)

    if voucher is None and user is None:
        print_info('*** - User Or Voucher Not Found ***')
        return (radiusd.RLM_MODULE_REJECT,
            (('Reply-Message', 'User account or Voucher does not exist.'),), (('Auth-Type', 'python'),)) 

    # Check whether AP allows user.
    print_info('*** AP Checking User Eligibility... ***')
    if not check_user_eligibility_on_ap(user, ap):
        print_info('*** - AP Disallowed User ***')
        return (radiusd.RLM_MODULE_REJECT,
            (('Reply-Message', 'User Unauthorized.'),), (('Auth-Type', 'python'),))
    else:
        print_info('*** - AP Allowed User ***')

    if user:
        print_info('*** - User fetched successfully: ' + user.username + ' ***')

        # Check Password
        print_info('*** Checking Password... ***')
        code = check_user_password(user, password)
        if code in REPLY_CODES_MESSAGES:
            print_info('*** - ' + REPLY_CODES_MESSAGES[code] + ' ***')
            return display_reply_message(code)
        else:
            print_info('*** - User Password Correct :-) ***')

        # Check User Account Status
        print_info('*** Checking User Account Status... ***')
        code = check_user_account_status(user)
        if code in REPLY_CODES_MESSAGES:
            print_info('*** - ' + REPLY_CODES_MESSAGES[code] + ' ***')
            return display_reply_message(code)
        else:
            print_info('*** - User Account Active ***')

        subscription = get_user_subscription(user)

    elif voucher:
        print_info('*** - Voucher fetched successfully: ' + voucher.username + ' ***')

        # Check Password
        print_info('*** Checking Password... ***')
        code = check_voucher_password(str(voucher.value), password)
        if code in REPLY_CODES_MESSAGES:
            print_info('*** - ' + REPLY_CODES_MESSAGES[code] + ' ***')
            return display_reply_message(code)
        else:
            print_info('*** - Voucher Password Correct :-) ***')
            

        print_info('*** Checking User Account Status... ***')
        print_info("*** We're skipping account status check for voucher ***")

        subscription = get_or_create_subscription(voucher)

    if not subscription:
        print_info('*** User Has No Subscription... ***')
        return (radiusd.RLM_MODULE_REJECT,
                (('Reply-Message', "You have no subscription. Click 'Manage Account' below to recharge your account and purchase a package."),), (('Auth-Type', 'python'),))
    else:
        # Check subscription validity
        print_info('*** Check User Subscription Validity ... ***')
        response = check_subscription_validity(subscription, user)

        if response[0] == 2:
            print_info('*** - User Subscription Valid ***')
            print_info('*** - Sending Access-Accept to Meraki ***')
        else:
            print_info('*** - User Subscription Invalid ***')
            print_info('*** - Sending Access-Reject to Meraki ***')

        return response

def accounting(p):
    print_info("*** Response Content: " + str(p) + " ***")
    params = dict(p)

    username = trim_value(params['User-Name'])
    acct_status_type = params['Acct-Status-Type']
    acct_session_id = params['Acct-Session-Id']

    if acct_status_type == 'Stop' or acct_status_type == 'Interim-Update':
        radcheck = Radcheck.objects.get(username__exact=username)
        data_usage = (int(params['Acct-Input-Octets']) + int(params['Acct-Output-Octets'])) / 1000000000.0

        user = getattr(radcheck, 'user', None)

        if user is not None:
            # Group user
            if user.subscriber.group is not None:
                group = GroupAccount.objects.get(name__exact=user.subscriber.group.name)
                usage = Decimal(data_usage) - group.data_usage

                data_balance = group.data_balance - usage
                group.data_usage = Decimal(data_usage)

                if data_balance <= 0:
                    group.data_balance = 0
		    #######
		    print_info('*** - Sending Disconnect-Request to Meraki ***')
		    if acct_status_type == 'Interim-Update':
		        output = send_disconnect_request(acct_session_id)
		        if output.find('Disconnect-ACK') != -1:
			    print_info("*** - User Disconnected ***")
		        else:
			    print_info("*** - User Disconnection Failed ***")
		    #######
                else:
                    group.data_balance = data_balance

                # Only group users are set logged in. So this
                # would make no difference with individual users.
	        radcheck.is_logged_in = False
                group.save()
            else:
                usage = Decimal(data_usage) - radcheck.data_usage

                data_balance = radcheck.data_balance - usage
                radcheck.data_usage = Decimal(data_usage)

                if data_balance <= 0:
                    radcheck.data_balance = 0
		    #######
		    print_info('*** - Sending Disconnect-Request to Meraki ***')
		    if acct_status_type == 'Interim-Update':
		        output = send_disconnect_request(acct_session_id)
		        if output.find('Disconnect-ACK') != -1:
			    print_info("*** - User Disconnected ***")
		        else:
			    print_info("*** - User Disconnection Failed ***")
		    #######
                else:
                    radcheck.data_balance = data_balance
            radcheck.save()
	else:
	    # Deal with instant vouchers here
	    pass

    if acct_status_type == 'Stop':
	if user is not None:
	    if user.subscriber.group is not None:
                group = GroupAccount.objects.get(name__exact=user.subscriber.group.name)
		group.data_usage = 0
		group.save()
	    else:
		radcheck.data_usage = 0
		radcheck.save()
	else:
	    # Deal with instant vouchers here
	    pass

    return radiusd.RLM_MODULE_OK

def recv_coa(p):
    print_info("*** Response Content - Receive CoA: " + str(p) + " ***")

def send_coa(p):
    print_info("*** Response Content - Send CoA: " + str(p) + " ***")
