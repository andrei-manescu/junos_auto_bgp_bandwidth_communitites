from jnpr.junos import Device
from jnpr.junos.utils.config import Config
from jnpr.junos.exception import *
from junos import Junos_Trigger_Event 
from lxml import etree
from time import sleep

import re
import os
import jcs
import sys
import argparse


'''
Version: 2.1
Description:
    Event based script that updates bgp bw communities dynamically when LAG link speed changes.
Requirements:
    - Junos 16.1+ (python).
    - First term of the import policy MUST delete all other bw communities (residue that neighbor might send and that could break UCMP).
      This is done by defining a wildcard all_bw_communities with members bandwidth:*:* and deleting it in the first policy       .
    - Regular Expression must be adjusted in the configuration.
    - Script username has to be configured as well as file ownership needs to be set.
    - Single hop BGP sessions (routing policy "from neighbor" creates the bw community context).
    - Debug should be disabled once tested.
    - BW communities should be set or added depending on case. CAUTION not to disturb other communities.
How it works:
    Event monitors <Bandwidth> events around ae IFDs (ignores IFL events) and calls the python script.
    The script uses the event and configured arguments as input.
    It checks the speed of the interface and converts it into community value.
    If IFL.0 or IFD description matches provided regex, script continues. Otherwise, it exits.
    The script then updates bgp bandwidth community name prefix+AE-IFD to current AE speed in Bps into dynamic database.
    Operator needs to use the community name into neighbor or group import policy (per case).
Notes:
    Requirement is that bw community uses bytes per second, but the range [0-4294967295] is not enough, 
    so the bw community value will be divided by 1000 (KB/s instead of B/s).
    Junos treats Link Bandwidth BGP community is a TRANSITIVE NLRI attribute, so export policies need to
    be adjusted to remove the bw communities before sending them off to peers (to avoid UCMP downstream).
    In order to change link-bw-community to non-transitive, following steps are required:
    a. Define a wildcard community to match all link-bandwidth communities (members bandwidth:*:*).
    b. Alter all export policies so that first term removes the community name matching all bw communities.

    Example:
    set policy-options community all_bw_communities members bandwidth:*:*
    set policy-options policy-statement nhs term 0 then community delete all_bw_communities
    set policy-options policy-statement nhs term 0 then next term
    set policy-options policy-statement nhs term 1 <do something>
    set policy-options policy-statement nhs term 2 <do something>
    <and so on>
    set protocols bgp group IBGP export nhs

**** IMPLEMENTATION STEP 1 ****
Copy this file to /var/db/scripts/event/ on both routing engines unless the "scripts synchronize" and "commit synchronize
are used.

**** IMPLEMENTATION STEP 2 ****
event-options {
    policy AE-BW-MON-AUTO {
        events SYSTEM;
        within 5 {
            not events SYSTEM;
        }
        attributes-match {
            system.message matches "EVENT <Bandwidth.*> ae[0-9]+ index";
        }
        then {
            event-script monitor_ae_bw_auto.py {
                arguments {
                    debug enable;
                    wait 10;
                    bgp_community_prefix bw_community_;
                    ae_intf_regex ".*fa[0-9]{2}[0-9]?.*";
                }
                output-filename TRIGGER_EVENT;
                destination TMP;
            }
        }
    }
    event-script {
        file monitor_ae_bw_auto.py {
            python-script-user amanescu;
        }
    }    
    destinations {
        TMP {
            archive-sites {
                /var/tmp/;
            }
        }
    }
}
system {
    scripts {
        language python;
    }
}
************************

**** IMPLEMENTATION STEP 3 ****
         ----ae0---
        /          \
 JUNOS                BGP NEighbor
        \          /
         ----ae1---
STANDARD CONFIGURATION DATABASE:
set policy-options community all_bw_communities members bandwidth:*:*
set policy-options policy-statement import-from-ISP-test term 0 then community delete all_bw_communities
set policy-options policy-statement export-to-1.1.0.2 dynamic-db
set policy-options policy-statement export-to-1.1.1.2 dynamic-db
set protocols bgp group RE2-test type external
set protocols bgp group RE2-test connect-retry-interval 1
set protocols bgp group RE2-test peer-as 10002
set protocols bgp group RE2-test multipath
set protocols bgp group RE2-test neighbor 1.1.0.2 export export-to-1.1.0.2
set protocols bgp group RE2-test neighbor 1.1.1.2 export export-to-1.1.1.2
set protocols bgp group RE2-test neighbor 1.1.0.4 local-address 1.1.0.3
set protocols bgp group RE2-test neighbor 1.1.0.4 export export-to-1.1.0.2
set protocols bgp group RE2-test neighbor 1.1.1.4 local-address 1.1.1.3
set protocols bgp group RE2-test neighbor 1.1.1.4 export export-to-1.1.0.2
DYNAMIC CONFIGURATION DATABASE:
set policy-options policy-statement export-to-1.1.0.2 term 1 from protocol aggregate
set policy-options policy-statement export-to-1.1.0.2 term 1 then community add bw_community_ae0
set policy-options policy-statement export-to-1.1.0.2 term 1 then accept
set policy-options policy-statement export-to-1.1.1.2 term 1 from protocol aggregate
set policy-options policy-statement export-to-1.1.1.2 term 1 then community add bw_community_ae1
set policy-options policy-statement export-to-1.1.1.2 term 1 then accept
set policy-options community bw_community_ae0 members bandwidth:10001:125000
set policy-options community bw_community_ae1 members bandwidth:10001:250000

************************

**** RESULT ****
[edit]
amanescu@RE0-test# run show route 10.0.0.1 extensive | match balance
                Next hop: 1.1.0.1 via ae0.0 balance 25%
                Next hop: 1.1.1.1 via ae1.0 balance 75%, selected

# set policy-options community bw_community_ae0 members bandwidth:10002:125000000
# set policy-options community bw_community_ae1 members bandwidth:10002:375000000

RE2-Arista(config-router-bgp)#do sh ip ro

VRF: default
Codes: C - connected, S - static, K - kernel,
       O - OSPF, IA - OSPF inter area, E1 - OSPF external type 1,
       E2 - OSPF external type 2, N1 - OSPF NSSA external type 1,
       N2 - OSPF NSSA external type2, B I - iBGP, B E - eBGP,
       R - RIP, I L1 - IS-IS level 1, I L2 - IS-IS level 2,
       O3 - OSPFv3, A B - BGP Aggregate, A O - OSPF Summary,
       NG - Nexthop Group Static Route, V - VXLAN Control Service,
       DH - Dhcp client installed default route

Gateway of last resort is not set

 C      1.1.0.0/24 is directly connected, Port-Channel1
 C      1.1.1.0/24 is directly connected, Port-Channel2
 C      2.2.0.0/24 is directly connected, Port-Channel3
 C      2.2.1.0/24 is directly connected, Port-Channel4
 B E    10.0.0.0/8 [200/0] via 1.1.0.1, Port-Channel1, weight 1/3
                           via 1.1.1.1, Port-Channel2, weight 2/3
 C      172.16.0.0/24 is directly connected, Management1

'''
parser = argparse.ArgumentParser()
parser.add_argument('-debug', required=True)
parser.add_argument('-wait', required=True)
parser.add_argument('-bgp_community_prefix', required=True)
parser.add_argument('-ae_intf_regex', required=True)
args = parser.parse_args()

def log(i, log_type, log_line):
    if args.debug == "enable":
        jcs.syslog("172", os.path.basename(__file__)+": iteration "+str(i)+":"+log_type+": "+log_line)
    else:
        return

def main():
    i = 1
    event_message = str(Junos_Trigger_Event.xpath('//trigger-event/message')[0].text)
    event_message_log = event_message.replace(" ","_")
    facility = str(Junos_Trigger_Event.xpath('//trigger-event/facility')[0].text)
    
    '''BW Community prefix is configurable but, to be safe, let's stick to a-zA-Z_ range '''
    if re.match('^[a-zA-Z_]{2,}$', args.bgp_community_prefix):
        bw_community_prefix = args.bgp_community_prefix
    else:
        log(i, 'ERROR', 'BW Community prefix is not valid. Using default.')
        bw_community_prefix = 'bw_community_'

    ''' Sanity check on ae interface name '''
    ae_ifd = re.search('Bandwidth.*> (ae[0-9]+) index', event_message)
    if ae_ifd:
        ae_ifd = ae_ifd.group(1)
        ''' Go with a syslog event regardless if debugging is enabled '''
        jcs.syslog("172", os.path.basename(__file__)+": Executed for AE"+str(ae_ifd))
        #log(i, 'DEBUG', "AE_IFD:"+str(ae_ifd))
        log(i, 'DEBUG', "Connecting to device and retrieving speed of "+str(ae_ifd))
    else:
        log(i, 'ERROR', "COULD NOT RETRIEVE AE NAME FROM EVENT")
        sys.exit()

    ''' Connect to device '''
    dev = Device(gather_facts=False).open(normalize=True)
    with Config(dev, mode='dynamic') as cu:  

        log(i, 'DEBUG', "Connection successful")

        ''' Sleep 2 seconds and wait for RPD to update AE speed '''
        log(i, 'DEBUG', "Sleeping 2 seconds")
        sleep(1)

        ''' Let's retrieve AE speed and description '''
        log(i, 'DEBUG', "Retrieving aggregate "+str(ae_ifd)+" information")
        agg_show = dev.rpc.get_interface_information(interface_name=ae_ifd)
        agg_speed = agg_show.xpath('string(//physical-interface/speed)')
        agg_speed_bps = agg_speed.replace("Unspecified","0")
        agg_speed_bps = agg_speed_bps.replace("Gbps","")
        ''' Per https://www.juniper.net/documentation/en_US/junos/topics/example/bgp-multipath-unequal.html, bw community
            second number represents value in bytes per second in the [0-4294967295] (34Gbps) range. '''
        if agg_speed_bps > 0:
             agg_speed_bps = int(agg_speed_bps)*1000000/8
        log(i, 'DEBUG', "Interface "+str(ae_ifd)+" speed is "+str(agg_speed_bps))

        ''' If AE.0 (Design of this script checks unit 0 description) description does not match specific string, I'm not interested in this LAG '''
        agg_description = agg_show.xpath("physical-interface/logical-interface[name='"+str(ae_ifd)+".0']/description")
        if len(agg_description) == 0:
            log(i, 'ERROR', "Interface "+str(ae_ifd)+" has no description under unit 0. Trying the IFD.")
            agg_description = agg_show.xpath("physical-interface[name='"+str(ae_ifd)+"']/description")
            if len(agg_description) == 0:
                log(i, 'ERROR', "Interface "+str(ae_ifd)+" IFD has no description either. I'm confused, so I will exit to avoid problems.")
                return
            agg_description = agg_description[0].text
        elif len(agg_description) == 1:
            agg_description = agg_description[0].text
            log(i, 'DEBUG', "Interface "+str(ae_ifd)+" IFD has description:"+str(agg_description))
        else:
            sys.exit()

        ''' Check if IFD/IFL.0 description matches requirements '''
        regex = re.compile('%s'%str(args.ae_intf_regex))
        if not regex.match(agg_description):
            log(i, 'DEBUG', "Not interested in AE >"+str(ae_ifd)+"< Description >"+agg_description+"< Regex>"+args.ae_intf_regex+"<")
            return
        log(i, 'DEBUG', "Aggregate Speed:"+str(agg_speed_bps)+" and aggregate description:"+str(agg_description)+". I'm interested in it.")

        ''' Retrieve AS number '''
        ASN = dev.rpc.get_config(filter_xml=etree.XML('<configuration><routing-options><autonomous-system/></routing-options></configuration>'),options={'inherit':'inherit','database':'committed'})
        ASN = ASN.xpath('string(//routing-options/autonomous-system/as-number)')
        log(i, 'DEBUG', "Our AS is:"+str(ASN))
        log(i, 'DEBUG', "All information retrieved. Building configuration.")

        ''' Build BW Community config '''
        config_xml = """
            <configuration>
                <policy-options>
                    <community replace="replace">
                        <name>{0}</name>
                        <members>bandwidth:{1}:{2}</members>
                    </community>
                </policy-options>
            </configuration>
        """.format(str(bw_community_prefix)+str(ae_ifd),ASN,str(agg_speed_bps))

        ''' If Commit DB Lock fails, wait configurable number of seconds '''
        log(i, 'DEBUG', "Entering wait loop. Wait time to commit (if db is locked) is:"+str(args.wait))

        while i <= int(args.wait):

            ''' Loading configuration '''
            log(i, 'DEBUG', "Loading configuration changes")
            try:
                cu.load(config_xml, format="xml", merge=False)
            except ConfigLoadError as err:
                log(i, 'DEBUG', "Building configuration")
                '''try:
                    dev.cu.unlock()
                except UnlockError:
                    log(i, 'ERROR', "Unable to unlock configuration")
                dev.close()'''
                return

            ''' Commit configuration '''
            log(i, 'DEBUG', "Committing the configuration")
            try:
                cu.commit()
                return
            except CommitError as err:
                ''' This prins the commit error to script output captured in destination directory present in configuration (TMP) '''
                print (format(err))
                log(i, 'ERROR', "Unable to commit configuration. Unlocking the configuration")
                try:
                    dev.cu.unlock()
                except UnlockError:
                    log(i, 'ERROR', "Unable to unlock configuration")
                dev.close()
                return



        '''dev.close()'''


if __name__ == "__main__":
    main()
