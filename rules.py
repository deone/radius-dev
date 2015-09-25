#! /usr/bin/env python

import os
import sys

import radiusd

p = (('Acct-Session-Id', '"624874448299458941"'), ('Called-Station-Id', '"00-18-0A-F2-DE-20:Radius test"'), ('Calling-Station-Id', '"48-D2-24-43-A6-C1"'), ('Framed-IP-Address', '172.31.3.142'), ('NAS-Identifier', '"Cisco Meraki cloud RADIUS client"'), ('NAS-IP-Address', '108.161.147.120'), ('NAS-Port', '0'), ('NAS-Port-Id', '"Wireless-802.11"'), ('NAS-Port-Type', 'Wireless-802.11'), ('Service-Type', 'Login-User'), ('User-Name', '"erica.nortey420140943@koforiduapoly.edu.gh"'), ('User-Password', '"SH9G3I"'),  ('Attr-26.29671.1', '0x446a756e676c65204851203032'))

def make_env():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billing.settings")

    import django
    django.setup()

def get_user(username):
    make_env()
    from django.contrib.auth.models import User
    return User.objects.get(username__exact=username)

def get_ap(mac):
    make_env()
    from accounts.models import AccessPoint
    return AccessPoint.objects.get(mac_address__exact=mac)

def create_mac(param):
    called_station_id = trim_value(param).split(':')[0]
    return called_station_id.replace('-', ':')

def trim_value(val):
    return val[1:-1]

def get_subscription(user):
    if user.subscriber.group is not None:
        # User belongs to a group. Return group package subscription
        subscription = user.subscriber.group.grouppackagesubscription_set.all()[0]
    else:
        subscription = user.subscriber.packagesubscription_set.all()[0]

    return subscription

def instantiate(p):
    print "*** instantiate ***"
    print p

def authorize(p):
    radiusd.radlog(radiusd.L_INFO, "*** authorize ***")
    radiusd.radlog(radiusd.L_INFO, "*** Request Content: " + str(p) + " ***")
    radiusd.radlog(radiusd.L_INFO, "*** Setting up env and importing Django ***")
    make_env()
    radiusd.radlog(radiusd.L_INFO, "*** Django imported successfully ***")

    from django.utils import timezone
    radiusd.radlog(radiusd.L_INFO, "*** Timezone module imported successfully ***")

    params = dict(p)
    radiusd.radlog(radiusd.L_INFO, "*** Request Content Dictionary: " + str(params) + " ***")

    username = trim_value(params['User-Name'])
    ap_mac = create_mac(params['Called-Station-Id'])

    user = get_user(username)
    radiusd.radlog(radiusd.L_INFO, '*** User fetched successfully: ' + user.username + ' ***')

    ap = get_ap(ap_mac)
    radiusd.radlog(radiusd.L_INFO, '*** AP fetched successfully: ' + ap.mac_address + ' ***')

    if user.is_active:
        radiusd.radlog(radiusd.L_INFO, '*** User is active ***')
        if ap.allows(user):
            radiusd.radlog(radiusd.L_INFO, '*** AP allowed user ***')
            subscription = get_subscription(user)
            if subscription.is_valid():
                radiusd.radlog(radiusd.L_INFO, '*** User subscription valid ***')
                now = timezone.now()

                package_period = str((subscription.stop - now).total_seconds())
                package_period = package_period.split(".")[0]

                bandwidth_limit = str(float(subscription.package.speed) * 1000000)
                bandwidth_limit = bandwidth_limit.split('.')[0]
                
                return (radiusd.RLM_MODULE_OK,
                    (('Session-Timeout', package_period),('Maximum-Data-Rate-Upstream', bandwidth_limit),('Maximum-Data-Rate-Downstream', bandwidth_limit)),
                    (('Auth-Type', 'python'),))
            else:
                radiusd.radlog(radiusd.L_INFO, '*** User subscription invalid ***')
                return (radiusd.RLM_MODULE_REJECT,
                    (('Reply-Message', 'Subscription Invalid'),), (('Auth-Type', 'python'),))
        else:
            radiusd.radlog(radiusd.L_INFO, '*** AP disallowed user ***')
            return (radiusd.RLM_MODULE_REJECT,
                (('Reply-Message', 'User Unauthorized'),), (('Auth-Type', 'python'),))
    else:
        radiusd.radlog(radiusd.L_INFO, '*** User is inactive ***')
        return (radiusd.RLM_MODULE_REJECT,
            (('Reply-Message', 'User De-activated'),), (('Auth-Type', 'python'),))

if __name__ == "__main__":
    a = authorize(p)
    print a
