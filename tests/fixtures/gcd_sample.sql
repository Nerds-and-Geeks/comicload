INSERT INTO `gcd_publisher` (`id`, `name`) VALUES (1,'Marvel'),(2,'Image Comics');
INSERT INTO `gcd_series` (`id`, `name`, `publisher_id`, `year_began`) VALUES (10,'The Punisher',1,2000),(11,'Alex + Ada',2,2013);
INSERT INTO `gcd_issue` (`id`, `number`, `series_id`, `on_sale_date`, `barcode`) VALUES (100,'12',10,'2001-03-01','75960608457000111'),(101,'2',11,'2013-12-11','70985301491000211');
