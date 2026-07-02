
# tap-toast

Tap for [Toast Data](https://pos.toasttab.com/).

## Requirements

- pip3
- python 3.5+
- mkvirtualenv

## Installation

In the directory:

```
$ mkvirtualenv -p python3 tap-toast
$ pip3 install -e .
```

## Usage

### Create config file

You can get all of the below from talking to a sales representative at Toast (totally obnoxious, I know).

```
{
  "client_id": "***",
  "client_secret": "***",
  "location_guid": "***",
  "management_group_guid": "***"
  "start_date": "2018-11-12T11:00:30+00:00"
}
```

The `location_guid` is the primary id for the restaurant, which is necessary to access the API.

The `management_group_guid` is the primary id for the restaurant group. It's required to get data on all restaurants within the group.

Toast is one of those companies where the API can only be accessed by talking to their sales team and signing an sales contract. Once the contract is in place, then their sales team will set up your account and email you the credentials necessary. **You will not be able to generate these keys on your own in the development portal.**

Here is an example of the credentials that the Toast sales team will provide you:

```
client ID: your-client-id
client secret: *FHHCsdqpme!@*$#
location GUID: 93djm422-bdu4-mpt3-148s-34ctcm8mp4jf
```

The `start_date` is just the date you want the sync to begin. You can select this yourself.

```
start_date: 2018-11-12T11:00:30+00:00
```

### 1- Discovery mode



```
$ tap-toast --config config.json --discover > catalog.json
```

### 2- Singer Discover Utility



```
$ singer-discover --input catalog.json --output catalog.json
```



### 3- Sync Mode

With an annotated `catalog.json`, the tap can be invoked in sync mode:

```
$ tap-toast --config config.json --catalog catalog.json > output/output.jsonl
```





