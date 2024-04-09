import argparse
import h5py
import numpy as np
import os
import shutil
import sncosmo
import healpy as hp

def get_str_dtype(arr):
    str_max_len = int(np.char.str_len(arr).max())
    return h5py.string_dtype(encoding='utf-8', length=str_max_len)

def convert_dtype(arr):
    if np.issubdtype(arr.dtype, np.floating):
        dtype = np.float32
    elif np.issubdtype(arr.dtype, np.str_):
        dtype = get_str_dtype(arr)
    else:
        dtype = arr.dtype
    return arr.astype(dtype)

def main(args):
    # Retrieve file paths
    file_dir = args.yse_data_path
    files = os.listdir(file_dir)
    num_examples = len(files)

    # Load example data to determine keys in the dataset
    example_metadata, example_data = sncosmo.read_snana_ascii(os.path.join(file_dir, files[0]), default_tablename='OBS')
    example_data = example_data['OBS']
    keys_metadata = list(example_metadata.keys())
    keys_data = list(example_data.keys())

    # Set which keys will be ignored when loading/converting/saving the data
    ignored_keys = [
        'END',
        'FIELD',
        'FLAG',
        'MASK_USED',
        '#_keywords_from_LC_processing'
        '#_PHOTCAT',
        '#_CNTRD_FLUX_OFFSET',
        '#_HOSTNAME',
        '#_IMSIZE_PIX',
        '#_DIST_FROM_CENTER_DEG',
    ]

    # Remove ignored keys
    for key in keys_metadata:
        if key in ignored_keys:
            keys_metadata.remove(key)
    for key in keys_data:
        if key in ignored_keys:
            keys_data.remove(key)

    # Define keys that comprise the standard lightcurve data
    keys_lightcurve = ['MJD', 'FLUXCAL', 'FLUXCALERR']

    # Initialize dictionaries to store data and metadata
    data = dict(zip(keys_data, ([] for _ in keys_data)))
    metadata = dict(zip(keys_metadata, ([] for _ in keys_metadata)))

    for file in files:
        # Load data and metadata from snana files using functionality from sncosmo
        metadata_, data_ = sncosmo.read_snana_ascii(os.path.join(file_dir, file), default_tablename='OBS')
        data_ = data_['OBS']
        # Iterate over keys and append data to the corresponding list in data / metadata dicts
        for key in keys_data:
            if key in data_.keys(): data[key].append(data_[key].data)
            else: data[key].append(np.full(0, np.nan))
            # The data are astropy columns wrapping numpy arrays which are accessed via .data
        for key in keys_metadata:
            if key in metadata_.keys(): metadata[key].append(metadata_[key])
            else: metadata[key].append(np.nan)

    # Create an array of all bands in the dataset
    all_bands = np.unique(np.concatenate(data['FLT']))

    """
    # Determine the length of the longest light curve (in a specific band) in the dataset
    max_length = 0
    for i in range(num_examples):
        _, count = np.unique(data['FLT'][i], return_counts=True)
        max_length = max(max_length, count.max(initial=0))
    """

    # Remove band from field_data as the timeseries will be arranged by band
    keys_data.remove('FLT')

    # Initialize lists to store lightcurve data
    lightcurve = []
    lightcurve_additional = []

    for i in range(num_examples):
        _, count = np.unique(data['FLT'][i], return_counts=True)
        max_length = count.max()
        mask = np.expand_dims(all_bands, 1) == data['FLT'][i] # Create mask to select data from each timeseries by band
        data_block = []  # Stores all timeseries in lightcurve for this example
        data_block_additional = []  # Stores all timeseries in lightcurve_additional for this example
        for key in keys_data:
            d = []  # Stores a particular timeseries (corresponding to the key) for each band
            for j in range(len(all_bands)):
                d_ = data[key][i][mask[j]]  # Select samples from timeseries for a specific band
                d_ = np.pad(d_,
                            (0, max_length - len(d_)),
                            mode='constant',
                            constant_values=-99 if key == 'MJD' else 0
                            )  # Pad band timeseries to the length of the longest timeseries
                d.append(d_)
            # Append complete timeseries organised by band to relevant list storing lightcurve(_additional)
            if key in keys_lightcurve:
                data_block.append(np.expand_dims(np.array(d), 1))
            else:
                data_block_additional.append(np.expand_dims(np.array(d), 1))
        # Expand dims of data_block(_additional) in preparation to concatenate over examples along dim 1
        # Also cast to required data type
        data_block = np.concatenate(data_block, 1, dtype=np.float32)
        data_block_additional = np.concatenate(data_block_additional, 1, dtype=np.float32)
        # Append complete lightcurve(_additional) for this example to the relevant list storing all examples
        lightcurve.append(data_block)
        lightcurve_additional.append(data_block_additional)

    """
    # Convert lightcurve (core and additional) data to numpy array
    lightcurve = np.array(lightcurve, dtype=np.float32)
    lightcurve_additional = np.array(lightcurve_additional, dtype=np.float32)
    """

    # Convert metadata to numpy arrays and cast to required datatypes
    for key in keys_metadata:
        metadata[key] = convert_dtype(np.array(metadata[key]))
    
    # Add numeric object_id to metadata (integer for each example in order of reading files)
    metadata['object_id'] = np.arange(1, num_examples + 1)

    # Add healpix to metadata
    metadata['healpix'] = hp.ang2pix(16, metadata['RA'], metadata['DECL'], lonlat=True, nest=True)

    # Cast bands to required datatype
    all_bands = convert_dtype(all_bands)

    # Establish conversions to standard names
    keys_all = keys_metadata + keys_data
    name_conversion = dict(zip(keys_all, keys_all))
    name_conversion.update({
        'RA': 'ra',
        'DECL': 'dec',
    })

    # Make output directories labelled by healpix
    unique_healpix = np.unique(metadata['healpix'])
    healpix_num_digits = len(str(hp.nside2npix(16)))
    for healpix in unique_healpix:
        healpix = str(healpix).zfill(healpix_num_digits)
        os.makedirs(os.path.join(args.output_dir, f'healpix={healpix}'), exist_ok=True)

    # Save data as hdf5 grouped into directories by healpix
    object_id_num_digits = len(str(num_examples))
    for i in range(num_examples):
        healpix = str(metadata['healpix'][i]).zfill(healpix_num_digits)
        object_id = str(metadata['object_id'][i]).zfill(object_id_num_digits)
        path = os.path.join(args.output_dir, f'healpix={healpix}', f'example_{object_id}.h5')
        with h5py.File(path, 'w') as hdf5_file:
            # Save metadata
            for key in keys_metadata:
                hdf5_file.create_dataset(name_conversion[key], data=metadata[key][i])
            # Save bands
            hdf5_file.create_dataset('bands', data=all_bands)
            # Save core timeseries
            hdf5_file.create_dataset('lightcurve', data=lightcurve[i])
            # Save additional timeseries
            hdf5_file.create_dataset('lightcurve_additional', data=lightcurve_additional[i])

    # Remove original data (data has now been reformatted and saved as hdf5)
    shutil.rmtree(args.yse_data_path)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extract YSE data and convert to standard time-series data format.')
    parser.add_argument('yse_data_path', type=str, help='Path to the local copy of the YSE DR1 data')
    parser.add_argument('output_dir', type=str, help='Path to the output directory')
    args = parser.parse_args()

    main(args)
