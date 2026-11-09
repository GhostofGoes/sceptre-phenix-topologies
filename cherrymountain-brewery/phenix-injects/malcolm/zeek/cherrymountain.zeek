##! CherryMountain baseline detections for Zeek (DESIGN.md 14). Malcolm loads this
##! through zeek/custom/__load__.zeek, and the notices reach the Notice log in
##! OpenSearch. The Suricata rules next to this file (cherrymountain.rules) cover
##! the same use cases for Malcolm's alerts; Zeek adds what a rule cannot see:
##! the BVLC function of every BACnet message, new hosts on a field VLAN, and
##! the size of an OpenPLC REST session.
##!
##! The address sets are the plant's inventory (DESIGN.md 10.2). They are `&redef`,
##! so a site can change them from a later script without editing this one.

@load base/frameworks/notice
@load base/protocols/conn
@load base/protocols/ssl
@load base/protocols/modbus

module CherryMountain;

export {
    redef enum Notice::Type += {
        ## Read, write, or delete of the BDT or the foreign device table, or a
        ## foreign-device registration, from anything but the two BBMDs. A rewritten
        ## BDT can redirect forwarded broadcasts, and a foreign device receives
        ## copies of them.
        BACnet_BBMD_Management,
        ## A Forwarded-NPDU from a host that is not one of the two BBMDs.
        BACnet_Forwarded_NPDU_Unexpected,
        ## UDP 47808 between two subnets outside the BACnet rows of the conduit
        ## matrix (DESIGN.md 10.3).
        BACnet_Cross_Zone_Unexpected,
        ## A BACnet WriteProperty or WritePropertyMultiple request from a host that
        ## is not on the writer list.
        BACnet_Write_Unexpected,
        ## A host spoke on a field VLAN that is not in the plant inventory.
        Field_Host_Unknown,
        ## A TLS session to an OpenPLC REST port from a host other than the EWS.
        PLC_REST_Unexpected_Client,
        ## A TLS session from the EWS to an OpenPLC REST port (baseline: program
        ## uploads and start or stop go over this port; the content is encrypted).
        PLC_REST_From_EWS,
        ## A session to an OpenPLC REST port that carried a program-sized upload.
        PLC_Program_Upload_Suspected,
        ## The ATS moved the plant to the emergency (genset) source.
        ATS_Transfer_To_Emergency,
        ## The ATS moved the plant back to the utility source.
        ATS_Transfer_To_Utility,
        ## plc-power started shedding the large feeders.
        Load_Shed_Active,
        ## plc-power stopped shedding load.
        Load_Shed_Cleared,
    };

    ## One bit of a Modbus read that carries a plant state change worth a notice.
    type WatchedBit: record {
        server: addr;       ##< Modbus server that answers
        fc: count;          ##< 1 (coils) or 2 (discrete inputs)
        address: count;     ##< PDU address of the bit (ot-sim uses the register number)
        name: string;       ##< Key for the state table
    };

    ## The two BBMDs: ot-svc (OPS) and bas-bbmd (BAS).
    const bbmd_hosts: set[addr] = { 10.20.30.13, 10.30.10.5 } &redef;
    ## BACnet clients that may write: bas-fuxa, scada-fuxa (the CO2 life-safety
    ## status, DESIGN.md 6.6), and the EWS.
    const bacnet_writers: set[addr] = { 10.30.10.10, 10.20.30.10, 10.20.30.12 } &redef;
    ## Hosts that may talk BACnet across the OPS/BAS boundary (conduit matrix).
    const bacnet_ops_peers: set[addr] = { 10.20.30.10, 10.20.30.12, 10.20.30.13 } &redef;
    const bacnet_ops_net: subnet = 10.20.30.0/24 &redef;
    const bacnet_bas_net: subnet = 10.30.10.0/24 &redef;

    ## The FIELD-* VLANs (unrouted islands) and every host that belongs on them.
    const field_nets: set[subnet] = {
        10.20.11.0/24, 10.20.12.0/24, 10.20.13.0/24,
        10.20.14.0/24, 10.20.15.0/24, 10.20.16.0/24,
    } &redef;
    const field_inventory: set[addr] = {
        10.20.11.1, 10.20.11.10,
        10.20.12.1, 10.20.12.10,
        10.20.13.1, 10.20.13.10,
        10.20.14.1, 10.20.14.10, 10.20.14.11,
        10.20.15.1, 10.20.15.10,
        10.20.16.1, 10.20.16.2, 10.20.16.20, 10.20.16.21, 10.20.16.22, 10.20.16.23,
    } &redef;

    ## State changes of the power system (DESIGN.md 6.6 and 14): gen-ats reports
    ## "ATS on the emergency source" as discrete input 10002 (plc-power polls it),
    ## and plc-power's own coil 7 is "load shed active" (FUXA polls it).
    const watched_bits: vector of WatchedBit = vector(
        [$server=10.20.16.23, $fc=2, $address=10002, $name="ats_on_emergency"],
        [$server=10.20.10.15, $fc=1, $address=7, $name="load_shed"]
    ) &redef;

    ## The engineering workstation, and the OpenPLC REST port.
    const ews_host: addr = 10.20.30.12 &redef;
    const plc_rest_port: port = 8443/tcp &redef;
    ## Bytes the originator sends in one REST session that mark a program upload
    ## (the cellar program ZIP is about 100 KB).
    const plc_upload_bytes: count = 50000 &redef;
}

## BVLC functions that manage the BDT or the foreign device table (requests only).
const bbmd_management_functions: set[count] = { 1, 2, 5, 6, 8 };
const bvlc_forwarded_npdu: count = 4;
## APDU type 0 is a confirmed request; service 15 is WriteProperty, 16 is
## WritePropertyMultiple.
const apdu_confirmed_request: count = 0;
const write_services: set[count] = { 15, 16 };

function bvlc_name(function_code: count): string
    {
    local names: table[count] of string = {
        [1] = "Write-Broadcast-Distribution-Table",
        [2] = "Read-Broadcast-Distribution-Table",
        [5] = "Register-Foreign-Device",
        [6] = "Read-Foreign-Device-Table",
        [8] = "Delete-Foreign-Device-Table-Entry",
        [4] = "Forwarded-NPDU",
    };
    return function_code in names ? names[function_code] : fmt("BVLC function 0x%02x", function_code);
    }

event bacnet_apdu_header(c: connection, is_orig: bool, bvlc_function: count,
                         pdu_type: count, pdu_service: count, invoke_id: count,
                         result_code: count)
    {
    # Only requests count. The ICSNPP analyzer reports both directions.
    if ( ! is_orig )
        return;

    local src = c$id$orig_h;

    if ( bvlc_function in bbmd_management_functions && src !in bbmd_hosts )
        NOTICE([$note=BACnet_BBMD_Management, $conn=c,
                $msg=fmt("%s from %s to %s (only the BBMDs manage the BDT)", bvlc_name(bvlc_function), src, c$id$resp_h),
                $identifier=cat(src, c$id$resp_h, bvlc_function),
                $suppress_for=5min]);

    if ( bvlc_function == bvlc_forwarded_npdu && src !in bbmd_hosts )
        NOTICE([$note=BACnet_Forwarded_NPDU_Unexpected, $conn=c,
                $msg=fmt("Forwarded-NPDU from %s to %s, which is not a BBMD", src, c$id$resp_h),
                $identifier=cat(src, c$id$resp_h, "forwarded"),
                $suppress_for=5min]);

    if ( pdu_type == apdu_confirmed_request && pdu_service in write_services && src !in bacnet_writers )
        NOTICE([$note=BACnet_Write_Unexpected, $conn=c,
                $msg=fmt("BACnet %s from %s to %s",
                         pdu_service == 15 ? "WriteProperty" : "WritePropertyMultiple", src, c$id$resp_h),
                $identifier=cat(src, c$id$resp_h, "write"),
                $suppress_for=5min]);
    }

event new_connection(c: connection)
    {
    local orig = c$id$orig_h;
    local resp = c$id$resp_h;

    # UDP 47808 across subnets is allowed only between scada-fuxa, the EWS, and
    # ot-svc on OPS and anything on BAS (both directions).
    if ( c$id$resp_p == 47808/udp && ! (orig in bacnet_ops_net && resp in bacnet_ops_net)
         && ! (orig in bacnet_bas_net && resp in bacnet_bas_net) )
        {
        local ok = (orig in bacnet_ops_peers && resp in bacnet_bas_net)
                   || (orig in bacnet_bas_net && resp in bacnet_ops_peers);
        if ( ! ok )
            NOTICE([$note=BACnet_Cross_Zone_Unexpected, $conn=c,
                    $msg=fmt("UDP 47808 from %s to %s is outside the BACnet conduits", orig, resp),
                    $identifier=cat(orig, resp),
                    $suppress_for=15min]);
        }

    # A new host on a field VLAN (an unrouted island that only PLCs, their I/O,
    # and the power devices belong on).
    if ( orig in field_nets && orig !in field_inventory )
        NOTICE([$note=Field_Host_Unknown, $conn=c,
                $msg=fmt("%s spoke on a field VLAN and is not in the plant inventory", orig),
                $identifier=cat(orig),
                $suppress_for=1hr]);
    }

event ssl_established(c: connection)
    {
    if ( c$id$resp_p != plc_rest_port )
        return;

    if ( c$id$orig_h == ews_host )
        NOTICE([$note=PLC_REST_From_EWS, $conn=c,
                $msg=fmt("OpenPLC REST session from the EWS to %s", c$id$resp_h),
                $identifier=cat(c$id$resp_h, "ews"),
                $suppress_for=1min]);
    else
        NOTICE([$note=PLC_REST_Unexpected_Client, $conn=c,
                $msg=fmt("OpenPLC REST session from %s to %s; only the EWS uses it", c$id$orig_h, c$id$resp_h),
                $identifier=cat(c$id$orig_h, c$id$resp_h),
                $suppress_for=5min]);
    }

event connection_state_remove(c: connection)
    {
    if ( c$id$resp_p != plc_rest_port || ! c?$orig || ! c$orig?$num_bytes_ip )
        return;

    # A program ZIP is the only thing that makes a client send this much.
    if ( c$orig$num_bytes_ip >= plc_upload_bytes )
        NOTICE([$note=PLC_Program_Upload_Suspected, $conn=c,
                $msg=fmt("%s sent %d bytes to the OpenPLC REST port of %s", c$id$orig_h, c$orig$num_bytes_ip, c$id$resp_h),
                $identifier=cat(c$id$orig_h, c$id$resp_h, "upload"),
                $suppress_for=1min]);
    }

# --- Modbus state changes (ATS transfer and load shed) -----------------------

# The last value seen of each watched bit, and the start address of each read
# that has not been answered yet (a response carries no address).
global bit_state: table[string] of bool;
global pending_reads: table[string] of count &create_expire=30sec;

function read_key(c: connection, tid: count, fc: count): string
    {
    return fmt("%s|%d|%d", c$uid, tid, fc);
    }

function bit_changed(c: connection, name: string, value: bool)
    {
    local first = name !in bit_state;
    local previous = first ? value : bit_state[name];
    bit_state[name] = value;

    # The first reading is the baseline, not an event.
    if ( first || previous == value )
        return;

    local note: Notice::Type;
    local what: string;
    if ( name == "ats_on_emergency" )
        {
        note = value ? ATS_Transfer_To_Emergency : ATS_Transfer_To_Utility;
        what = value ? "the ATS moved to the emergency source" : "the ATS moved back to the utility source";
        }
    else
        {
        note = value ? Load_Shed_Active : Load_Shed_Cleared;
        what = value ? "load shed became active" : "load shed was cleared";
        }
    NOTICE([$note=note, $conn=c,
            $msg=fmt("%s (seen from %s to %s)", what, c$id$orig_h, c$id$resp_h),
            $identifier=cat(name, value, network_time())]);
    }

function check_bits(c: connection, tid: count, fc: count, bits: ModbusCoils)
    {
    local key = read_key(c, tid, fc);
    if ( key !in pending_reads )
        return;

    local start = pending_reads[key];
    delete pending_reads[key];

    for ( i in watched_bits )
        {
        local w = watched_bits[i];
        if ( w$server != c$id$resp_h || w$fc != fc )
            next;
        if ( w$address >= start && w$address - start < |bits| )
            bit_changed(c, w$name, bits[w$address - start]);
        }
    }

event modbus_read_coils_request(c: connection, headers: ModbusHeaders, start_address: count, quantity: count)
    {
    pending_reads[read_key(c, headers$tid, 1)] = start_address;
    }

event modbus_read_coils_response(c: connection, headers: ModbusHeaders, coils: ModbusCoils)
    {
    check_bits(c, headers$tid, 1, coils);
    }

event modbus_read_discrete_inputs_request(c: connection, headers: ModbusHeaders, start_address: count, quantity: count)
    {
    pending_reads[read_key(c, headers$tid, 2)] = start_address;
    }

event modbus_read_discrete_inputs_response(c: connection, headers: ModbusHeaders, coils: ModbusCoils)
    {
    check_bits(c, headers$tid, 2, coils);
    }
